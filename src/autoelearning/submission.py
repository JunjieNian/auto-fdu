from __future__ import annotations

import html
import json
import secrets
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .auth import ElearningSession
from .canvas import CanvasClient
from .config import Settings
from .pdf_export import render_markdown_pdf
from .storage import Store


class ApprovalError(RuntimeError):
    pass


class SubmissionLockedError(RuntimeError):
    pass


APPROVAL_TTL_MINUTES = 10


def approve_job(
    store: Store,
    job_id: int,
    *,
    reviewed: bool,
    confirmation: str,
    submission_type: str,
    artifact_paths: list[str],
    review_notes: str = "",
) -> dict[str, Any]:
    job = store.get_agent_job(job_id)
    if not job:
        raise ApprovalError("草稿任务不存在。")
    if job["status"] not in {"draft_ready", "approved"}:
        raise ApprovalError("只有已生成的草稿可以批准。")
    if job.get("test_mode"):
        raise ApprovalError("该草稿来自已完成作业测试模式，永久禁止批准或提交。")
    if not reviewed:
        raise ApprovalError("请先确认已经完整审查草稿和所有附件。")
    if confirmation.strip() != job["assignment_name"]:
        raise ApprovalError("确认文字必须与作业标题完全一致。")

    assignment = json.loads(job["assignment_raw_json"])
    allowed_types = assignment.get("submission_types") or []
    if submission_type not in {"online_text_entry", "online_upload"}:
        raise ApprovalError("当前只支持文本或文件提交。")
    if submission_type not in allowed_types:
        raise ApprovalError("该作业不允许选择的提交类型。")

    approved_artifacts = _validate_artifacts(job, artifact_paths)
    if submission_type == "online_upload" and not approved_artifacts:
        raise ApprovalError("文件提交至少需要选择一个已审查产物。")
    expected = job.get("submission_artifact_path")
    if (
        submission_type == "online_upload" and expected
        and expected not in {item.get("path") for item in approved_artifacts}
    ):
        raise ApprovalError("必须选择审查台当前显示的待提交 PDF。")
    if submission_type == "online_text_entry" and not job.get("draft_path"):
        raise ApprovalError("文本草稿不存在。")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=APPROVAL_TTL_MINUTES)
    token = secrets.token_urlsafe(32)
    store.update_agent_job(
        job_id,
        status="approved",
        submission_type=submission_type,
        approved_artifacts_json=approved_artifacts,
        review_notes=review_notes.strip(),
        approved_at=now.isoformat(),
        approval_token=token,
        approval_expires_at=expires.isoformat(),
        error=None,
    )
    return {"approval_token": token, "approval_expires_at": expires.isoformat()}


def update_reviewed_draft(store: Store, job_id: int, content: str) -> dict[str, Any]:
    job = store.get_agent_job(job_id)
    if not job or job["status"] not in {"draft_ready", "approved"}:
        raise ApprovalError("当前草稿不可编辑。")
    workspace = Path(job["workspace"]).resolve()
    output = (workspace / "output").resolve()
    output.relative_to(workspace)
    path = output / "reviewed-answer.md"
    path.write_text(content, encoding="utf-8")
    reviewed_pdf = output / "reviewed-answer.pdf"
    render_markdown_pdf(
        path,
        reviewed_pdf,
        title=job["assignment_name"],
        course=job["course_name"],
        submission_copy=True,
    )
    artifacts = job.get("artifacts") or []
    relative = "output/reviewed-answer.md"
    replacement = {
        "path": relative,
        "name": path.name,
        "size": path.stat().st_size,
        "content_type": "text/markdown",
    }
    artifacts = [item for item in artifacts if item.get("path") != relative] + [replacement]
    pdf_relative = "output/reviewed-answer.pdf"
    artifacts = [item for item in artifacts if item.get("path") != pdf_relative] + [{
        "path": pdf_relative,
        "name": reviewed_pdf.name,
        "size": reviewed_pdf.stat().st_size,
        "content_type": "application/pdf",
    }]
    store.update_agent_job(
        job_id,
        status="draft_ready",
        draft_path=str(path),
        artifacts_json=artifacts,
        submission_artifact_path=pdf_relative,
        approval_token=None,
        approval_expires_at=None,
        approved_at=None,
        approved_artifacts_json=None,
        submission_type=None,
    )
    return replacement


def submit_approved_job(
    settings: Settings,
    job_id: int,
    approval_token: str,
    *,
    username: str | None,
    password: str | None,
) -> dict[str, Any]:
    # This check intentionally happens before opening a browser session or making any request.
    if not settings.submission_enabled:
        raise SubmissionLockedError(
            "提交总开关处于关闭状态；本次不会上传或提交任何内容。"
        )

    with Store(settings.database_path) as store:
        job = store.get_agent_job(job_id)
        if not job or job["status"] != "approved":
            raise ApprovalError("该任务尚未获得提交批准。")
        if job.get("test_mode"):
            raise ApprovalError("测试模式任务永久禁止提交。")
        if not secrets.compare_digest(job.get("approval_token") or "", approval_token or ""):
            raise ApprovalError("批准令牌无效。")
        expires = datetime.fromisoformat(job["approval_expires_at"])
        if datetime.now(timezone.utc) >= expires:
            store.update_agent_job(job_id, status="draft_ready", approval_token=None)
            raise ApprovalError("批准已超过十分钟，请重新审查并批准。")
        store.update_agent_job(job_id, status="submitting", error=None)

    local_settings = replace(settings, username=username, password=password)
    try:
        with ElearningSession(local_settings) as session:
            client = CanvasClient(session)
            current = client.assignment(int(job["course_id"]), int(job["assignment_id"]))
            submission = current.get("submission") or {}
            if submission.get("workflow_state") in {"submitted", "graded"}:
                raise ApprovalError("eLearning 显示该作业已经提交或评分，已停止重复提交。")
            if current.get("locked_for_user"):
                raise ApprovalError("该作业当前已锁定，无法提交。")
            if job["submission_type"] not in (current.get("submission_types") or []):
                raise ApprovalError("作业允许的提交类型已经变化，请重新同步并审查。")

            if job["submission_type"] == "online_text_entry":
                draft = _safe_job_path(job, Path(job["draft_path"])).read_text(encoding="utf-8")
                result = client.submit_assignment(
                    int(job["course_id"]),
                    int(job["assignment_id"]),
                    "online_text_entry",
                    body=_markdown_as_safe_html(draft),
                )
            else:
                file_ids = []
                for artifact in job.get("approved_artifacts") or []:
                    path = _safe_job_path(job, Path(job["workspace"]) / artifact["path"])
                    uploaded = client.upload_submission_file(
                        int(job["course_id"]), int(job["assignment_id"]), path
                    )
                    file_ids.append(int(uploaded["id"]))
                result = client.submit_assignment(
                    int(job["course_id"]),
                    int(job["assignment_id"]),
                    "online_upload",
                    file_ids=file_ids,
                )
        with Store(settings.database_path) as store:
            store.update_agent_job(
                job_id,
                status="submitted",
                submitted_at=datetime.now(timezone.utc).isoformat(),
                submission_response_json=result,
                approval_token=None,
            )
        return result
    except Exception as exc:
        with Store(settings.database_path) as store:
            store.update_agent_job(job_id, status="submit_failed", error=str(exc))
        raise


def _validate_artifacts(job: dict[str, Any], paths: list[str]) -> list[dict[str, Any]]:
    available = {item.get("path"): item for item in (job.get("artifacts") or [])}
    selected = []
    for relative in paths:
        artifact = available.get(relative)
        if not artifact:
            raise ApprovalError(f"未知产物：{relative}")
        _safe_job_path(job, Path(job["workspace"]) / relative)
        selected.append(artifact)
    return selected


def _safe_job_path(job: dict[str, Any], path: Path) -> Path:
    workspace = Path(job["workspace"]).resolve()
    resolved = path.resolve()
    resolved.relative_to(workspace)
    if not resolved.is_file():
        raise ApprovalError(f"产物文件不存在：{resolved.name}")
    return resolved


def _markdown_as_safe_html(markdown: str) -> str:
    paragraphs = [part.strip() for part in markdown.split("\n\n") if part.strip()]
    return "".join(f"<p>{html.escape(part).replace(chr(10), '<br>')}</p>" for part in paragraphs)
