from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
import webbrowser
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from flask import Flask, jsonify, render_template, request, send_file

from .agent_workflow import CodexDraftAgent
from .assignment_gate import ELIGIBLE, evaluate_assignment
from .auth import AuthenticationError, ElearningSession
from .canvas import CanvasClient
from .config import Settings, load_settings
from .storage import Store
from .submission import (
    ApprovalError,
    SubmissionLockedError,
    approve_job,
    submit_approved_job,
    update_reviewed_draft,
)
from .workflow import sync_all

HOST = "127.0.0.1"
PORT = 8765
APP_URL = f"http://{HOST}:{PORT}"


class RuntimeState:
    def __init__(self) -> None:
        self.username: str | None = None
        self.password: str | None = None
        self.user: dict[str, Any] | None = None
        self.syncing = False
        self.agent_busy = False
        self.agent_queue: list[tuple[int, str]] = []
        self.lock = threading.Lock()


state = RuntimeState()
settings = load_settings()
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config.update(JSON_AS_ASCII=False)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"ok": True, "app": "autoelearning"})


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    if not username or not password:
        return _error("请输入学号和密码。", 400)
    local_settings = replace(settings, username=username, password=password)
    try:
        with ElearningSession(local_settings) as session:
            user = CanvasClient(session).get_json("/api/v1/users/self")
        state.username, state.password, state.user = username, password, user
        return jsonify({"ok": True, "user": _safe_user(user)})
    except AuthenticationError as exc:
        return _error(str(exc), 401)
    except Exception as exc:
        app.logger.exception("Login failed")
        return _error(f"连接 eLearning 失败：{exc}", 502)


@app.get("/api/session")
def session_status():
    if state.user:
        return jsonify({"authenticated": True, "user": _safe_user(state.user)})
    # A saved Canvas session can remain usable after the desktop app restarts.
    try:
        with ElearningSession(replace(settings, username=None, password=None)) as session:
            user = CanvasClient(session).get_json("/api/v1/users/self")
        state.user = user
        return jsonify({"authenticated": True, "user": _safe_user(user)})
    except Exception:
        return jsonify({"authenticated": False})


@app.post("/api/sync")
def sync():
    with state.lock:
        if state.syncing:
            return _error("同步正在进行中，请稍候。", 409)
        state.syncing = True
    local_settings = replace(settings, username=state.username, password=state.password)
    try:
        with Store(local_settings.database_path) as store:
            existing_ids = store.assignment_ids()
            run_id = store.start_run()
            try:
                with ElearningSession(local_settings) as session:
                    client = CanvasClient(session)
                    user = client.get_json("/api/v1/users/self")
                    summary, errors = sync_all(client, store)
                state.user = user
                new_ids = store.assignment_ids() - existing_ids
                gate_summary, auto_job_ids = _apply_assignment_gate(store, new_ids)
                result = {
                    **summary.__dict__, "errors": errors, "gate": gate_summary,
                    "auto_draft_job_ids": auto_job_ids,
                }
                store.finish_run(run_id, "partial" if errors else "success", result)
            except Exception as exc:
                store.finish_run(run_id, "failed", {"error": str(exc)})
                raise
        if auto_job_ids:
            _enqueue_agent_jobs(auto_job_ids)
        return jsonify({"ok": not errors, **result})
    except AuthenticationError as exc:
        state.user = None
        return _error(str(exc), 401)
    except Exception as exc:
        app.logger.exception("Sync failed")
        return _error(f"同步失败：{exc}", 500)
    finally:
        with state.lock:
            state.syncing = False


@app.post("/api/agent/jobs")
def create_agent_job():
    data = request.get_json(silent=True) or {}
    try:
        assignment_id = int(data.get("assignment_id"))
    except (TypeError, ValueError):
        return _error("请选择一个有效作业。", 400)
    if not shutil.which("codex"):
        return _error("当前系统找不到 Codex CLI。", 503)
    with Store(settings.database_path) as store:
        assignment = store.get_assignment(assignment_id)
        if not assignment:
            return _error("本地没有该作业，请先同步。", 404)
        completed = assignment.get("submission_state") in {"submitted", "graded"}
        test_mode = data.get("test_mode") is True
        if completed and not test_mode:
            return _error("该作业已经提交或评分；如需验证，请使用永久不可提交的测试模式。", 409)
        if not completed:
            decision = evaluate_assignment(assignment)
            store.update_assignment_gate(
                assignment_id, status=decision.status, reason=decision.reason,
                evidence=list(decision.evidence), fingerprint=decision.fingerprint,
            )
            if decision.status != ELIGIBLE:
                return _error(f"自动门控已忽略此作业：{decision.reason}", 409)
        job_id = store.create_agent_job(
            assignment_id, int(assignment["course_id"]), test_mode=completed or test_mode
        )
    _enqueue_agent_jobs([job_id])
    return jsonify({"ok": True, "job_id": job_id, "status": "queued"}), 202


@app.get("/api/agent/jobs/<int:job_id>")
def agent_job_detail(job_id: int):
    with Store(settings.database_path) as store:
        job = store.get_agent_job(job_id)
        messages = store.list_agent_messages(job_id) if job else []
    if not job:
        return _error("草稿任务不存在。", 404)
    public = _public_job(job, include_detail=True)
    draft_path = job.get("draft_path")
    if draft_path:
        try:
            public["draft"] = _job_file(job, Path(draft_path)).read_text(encoding="utf-8")
        except Exception:
            public["draft"] = ""
    else:
        public["draft"] = ""
    public["messages"] = messages
    return jsonify(public)


@app.post("/api/agent/jobs/<int:job_id>/messages")
def continue_agent_job(job_id: int):
    data = request.get_json(silent=True) or {}
    content = str(data.get("content") or "").strip()
    if not content:
        return _error("请输入希望 Agent 修改的内容。", 400)
    if len(content) > 4000:
        return _error("单条修改要求不能超过 4000 个字符。", 400)
    if not shutil.which("codex"):
        return _error("当前系统找不到 Codex CLI。", 503)
    with Store(settings.database_path) as store:
        job = store.get_agent_job(job_id)
        if not job:
            return _error("草稿任务不存在。", 404)
        if job["status"] not in {"draft_ready", "approved", "failed"}:
            return _error("Agent 正在处理上一轮修改，请等待完成。", 409)
        if not job.get("workspace") or not Path(job["workspace"]).is_dir():
            return _error("原始 Agent 工作区不存在，无法继续对话。", 409)
        store.add_agent_message(job_id, "user", content)
        store.update_agent_job(
            job_id, status="queued_revision", pending_action="revision", error=None,
            approved_artifacts_json=None, submission_type=None, approved_at=None,
            approval_token=None, approval_expires_at=None,
        )
    _enqueue_agent_tasks([(job_id, "revision")])
    return jsonify({"ok": True, "job_id": job_id, "status": "queued_revision"}), 202


@app.patch("/api/agent/jobs/<int:job_id>/draft")
def edit_agent_draft(job_id: int):
    data = request.get_json(silent=True) or {}
    content = str(data.get("content") or "")
    if not content.strip():
        return _error("草稿内容不能为空。", 400)
    try:
        with Store(settings.database_path) as store:
            artifact = update_reviewed_draft(store, job_id, content)
        return jsonify({"ok": True, "artifact": artifact, "status": "draft_ready"})
    except ApprovalError as exc:
        return _error(str(exc), 409)


@app.post("/api/agent/jobs/<int:job_id>/approve")
def approve_agent_job(job_id: int):
    data = request.get_json(silent=True) or {}
    try:
        with Store(settings.database_path) as store:
            approval = approve_job(
                store,
                job_id,
                reviewed=data.get("reviewed") is True,
                confirmation=str(data.get("confirmation") or ""),
                submission_type=str(data.get("submission_type") or ""),
                artifact_paths=[str(value) for value in (data.get("artifact_paths") or [])],
                review_notes=str(data.get("review_notes") or ""),
            )
        return jsonify({"ok": True, "status": "approved", **approval})
    except ApprovalError as exc:
        return _error(str(exc), 409)


@app.post("/api/agent/jobs/<int:job_id>/submit")
def submit_agent_job(job_id: int):
    data = request.get_json(silent=True) or {}
    if data.get("confirm_submit") is not True:
        return _error("最终提交必须再次明确确认。", 400)
    try:
        result = submit_approved_job(
            settings,
            job_id,
            str(data.get("approval_token") or ""),
            username=state.username,
            password=state.password,
        )
        return jsonify({"ok": True, "status": "submitted", "submission": result})
    except SubmissionLockedError as exc:
        return _error(str(exc), 423)
    except ApprovalError as exc:
        return _error(str(exc), 409)
    except Exception as exc:
        app.logger.exception("Submission failed for job %s", job_id)
        return _error(f"提交失败：{exc}", 500)


@app.get("/api/agent/jobs/<int:job_id>/artifacts/<path:artifact_path>")
def download_agent_artifact(job_id: int, artifact_path: str):
    with Store(settings.database_path) as store:
        job = store.get_agent_job(job_id)
    if not job:
        return _error("草稿任务不存在。", 404)
    try:
        path = _job_file(job, Path(job["workspace"]) / artifact_path)
    except Exception:
        return _error("产物路径无效。", 404)
    preview = request.args.get("preview") == "1" and path.suffix.lower() == ".pdf"
    response = send_file(path, as_attachment=not preview, download_name=path.name)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _apply_assignment_gate(
    store: Store, new_assignment_ids: set[int],
) -> tuple[dict[str, int], list[int]]:
    summary = {"eligible": 0, "ignored": 0, "needs_context": 0, "new": len(new_assignment_ids)}
    auto_job_ids: list[int] = []
    codex_ready = shutil.which("codex") is not None
    for assignment in store.assignments_for_gate():
        decision = evaluate_assignment(assignment)
        store.update_assignment_gate(
            int(assignment["id"]), status=decision.status, reason=decision.reason,
            evidence=list(decision.evidence), fingerprint=decision.fingerprint,
        )
        if decision.status == ELIGIBLE:
            summary["eligible"] += 1
        elif decision.status == "needs_context":
            summary["needs_context"] += 1
        else:
            summary["ignored"] += 1
        if (
            int(assignment["id"]) in new_assignment_ids
            and decision.status == ELIGIBLE
            and settings.agent_auto_draft_enabled
            and codex_ready
            and not store.has_agent_job(int(assignment["id"]))
        ):
            auto_job_ids.append(store.create_agent_job(
                int(assignment["id"]), int(assignment["course_id"])
            ))
    summary["auto_queued"] = len(auto_job_ids)
    return summary, auto_job_ids


def _enqueue_agent_jobs(job_ids: list[int]) -> None:
    _enqueue_agent_tasks([(job_id, "draft") for job_id in job_ids])


def _enqueue_agent_tasks(tasks: list[tuple[int, str]]) -> None:
    if not tasks:
        return
    should_start = False
    with state.lock:
        known = set(state.agent_queue)
        state.agent_queue.extend(task for task in tasks if task not in known)
        if not state.agent_busy:
            state.agent_busy = True
            should_start = True
    if should_start:
        threading.Thread(
            target=_agent_queue_worker, name="draft-agent-queue", daemon=True
        ).start()


def _agent_queue_worker() -> None:
    while True:
        with state.lock:
            if not state.agent_queue:
                state.agent_busy = False
                return
            job_id, action = state.agent_queue.pop(0)
        try:
            agent = CodexDraftAgent(settings)
            if action == "revision":
                agent.revise_job(job_id)
            else:
                agent.run_job(job_id, state.username, state.password)
        except Exception:
            app.logger.exception("Agent job %s failed", job_id)


@app.get("/api/dashboard")
def dashboard():
    db = settings.database_path
    if not db.exists():
        return jsonify(_empty_dashboard())
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    try:
        counts = {
            name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in ("courses", "assignments", "announcements", "materials")
        }
        courses = _rows(connection, "SELECT id,name,course_code FROM courses ORDER BY name")
        assignments = _rows(
            connection,
            """SELECT a.id,a.course_id,a.name,a.due_at,a.html_url,a.submission_state,
            a.gate_status,a.gate_reason,a.gate_evidence_json,a.gate_evaluated_at,
            c.name AS course_name,c.course_code FROM assignments a
            JOIN courses c ON c.id=a.course_id ORDER BY CASE WHEN a.due_at IS NULL THEN 1 ELSE 0 END,
            datetime(a.due_at) DESC""",
        )
        announcements = _rows(
            connection,
            """SELECT a.id,a.course_id,a.title,a.posted_at,a.html_url,c.name AS course_name,
            c.course_code FROM announcements a JOIN courses c ON c.id=a.course_id
            ORDER BY datetime(a.posted_at) DESC""",
        )
        materials = _rows(
            connection,
            """SELECT m.item_key,m.course_id,m.kind,m.title,m.url,m.module_name,
            c.name AS course_name,c.course_code FROM materials m JOIN courses c ON c.id=m.course_id
            ORDER BY c.name,m.title""",
        )
        last_run = connection.execute(
            "SELECT finished_at,status,summary_json FROM sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        agent_jobs = []
        try:
            with Store(db) as store:
                agent_jobs = [_public_job(job) for job in store.list_agent_jobs()]
        except sqlite3.OperationalError:
            pass
        return jsonify(
            {
                "counts": counts,
                "courses": courses,
                "assignments": assignments,
                "announcements": announcements,
                "materials": materials,
                "last_run": dict(last_run) if last_run else None,
                "agent_jobs": agent_jobs,
                "agent": {
                    "busy": state.agent_busy,
                    "queue_size": len(state.agent_queue),
                    "codex_available": shutil.which("codex") is not None,
                    "submission_enabled": settings.submission_enabled,
                    "auto_draft_enabled": settings.agent_auto_draft_enabled,
                },
            }
        )
    finally:
        connection.close()


def _rows(connection: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query).fetchall()]


def _safe_user(user: dict[str, Any]) -> dict[str, Any]:
    return {"id": user.get("id"), "name": user.get("name"), "short_name": user.get("short_name")}


def _empty_dashboard() -> dict[str, Any]:
    return {
        "counts": {"courses": 0, "assignments": 0, "announcements": 0, "materials": 0},
        "courses": [], "assignments": [], "announcements": [], "materials": [], "last_run": None,
        "agent_jobs": [],
        "agent": {
            "busy": False, "queue_size": 0,
            "codex_available": shutil.which("codex") is not None,
            "submission_enabled": settings.submission_enabled,
            "auto_draft_enabled": settings.agent_auto_draft_enabled,
        },
    }


def _public_job(job: dict[str, Any], include_detail: bool = False) -> dict[str, Any]:
    keys = {
        "id", "assignment_id", "course_id", "status", "draft_path", "artifacts",
        "approved_artifacts", "submission_type", "review_notes", "approved_at",
        "approval_expires_at", "submitted_at", "error", "codex_exit_code", "created_at",
        "updated_at", "assignment_name", "due_at", "submission_state", "course_name",
        "course_code", "test_mode", "submission_artifact_path", "pending_action",
    }
    result = {key: job.get(key) for key in keys}
    if include_detail:
        assignment = json.loads(job.get("assignment_raw_json") or "{}")
        result["submission_types"] = assignment.get("submission_types") or []
        result["allowed_extensions"] = assignment.get("allowed_extensions") or []
        result["manifest"] = job.get("manifest")
    return result


def _job_file(job: dict[str, Any], path: Path) -> Path:
    workspace = Path(job["workspace"]).resolve()
    resolved = path.resolve()
    resolved.relative_to(workspace)
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


def _error(message: str, status: int):
    return jsonify({"ok": False, "error": message}), status


def _already_running() -> bool:
    try:
        with urlopen(f"{APP_URL}/health", timeout=1) as response:
            return json.loads(response.read()).get("app") == "autoelearning"
    except Exception:
        return False


def main() -> None:
    if _already_running():
        webbrowser.open(APP_URL)
        return
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    queued_tasks: list[tuple[int, str]] = []
    with Store(settings.database_path) as store:
        store.connection.execute(
            """UPDATE agent_jobs SET status='failed', error='应用重启中断了 Agent 任务', updated_at=?
            WHERE status IN ('preparing','running','revising','submitting')""",
            (datetime.now(timezone.utc).isoformat(),),
        )
        store.connection.commit()
        _apply_assignment_gate(store, set())
        queued_tasks = store.queued_agent_tasks()
    log_path = settings.data_dir / "desktop-app.log"
    logging.basicConfig(filename=log_path, level=logging.INFO, encoding="utf-8")
    _enqueue_agent_tasks(queued_tasks)
    threading.Timer(1.2, lambda: webbrowser.open(APP_URL)).start()
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
