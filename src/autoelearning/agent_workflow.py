from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .auth import ElearningSession
from .canvas import CanvasClient
from .config import Settings
from .pdf_export import render_markdown_pdf
from .storage import Store


class DraftAgentError(RuntimeError):
    pass


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)


def html_to_text(value: str | None) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(value or "")
    return "\n".join(parser.parts)


class CodexDraftAgent:
    """Run Codex in an isolated job directory and produce review-only artifacts."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def run_job(self, job_id: int, username: str | None, password: str | None) -> None:
        with Store(self.settings.database_path) as store:
            job = store.get_agent_job(job_id)
            if not job:
                raise DraftAgentError(f"Agent job {job_id} does not exist")
            assignment = store.get_assignment(int(job["assignment_id"]))
            if not assignment:
                raise DraftAgentError("Assignment is no longer available locally")
            workspace = self.settings.agent_jobs_dir / str(job_id)
            if workspace.exists():
                shutil.rmtree(workspace)
            (workspace / "context" / "attachments").mkdir(parents=True)
            (workspace / "context" / "materials").mkdir(parents=True)
            (workspace / "output").mkdir(parents=True)
            store.update_agent_job(job_id, status="preparing", workspace=str(workspace), error=None)

        try:
            manifest = self._prepare_context(
                workspace, assignment, username, password, test_mode=bool(job.get("test_mode"))
            )
            with Store(self.settings.database_path) as store:
                store.update_agent_job(job_id, status="running", manifest_json=manifest)
            exit_code, draft_path, artifacts = self._run_codex(workspace, manifest)
            if exit_code != 0:
                raise DraftAgentError(f"Codex exited with code {exit_code}; see codex.log")
            with Store(self.settings.database_path) as store:
                store.update_agent_job(
                    job_id,
                    status="draft_ready",
                    draft_path=str(draft_path),
                    artifacts_json=artifacts,
                    codex_exit_code=exit_code,
                )
        except Exception as exc:
            with Store(self.settings.database_path) as store:
                store.update_agent_job(job_id, status="failed", error=str(exc))
            raise

    def _prepare_context(
        self,
        workspace: Path,
        assignment_row: dict[str, Any],
        username: str | None,
        password: str | None,
        *,
        test_mode: bool,
    ) -> dict[str, Any]:
        local_settings = replace(self.settings, username=username, password=password)
        downloaded: list[dict[str, Any]] = []
        with ElearningSession(local_settings) as session:
            client = CanvasClient(session)
            assignment = client.assignment(
                int(assignment_row["course_id"]), int(assignment_row["id"])
            )
            for attachment in assignment.get("attachments") or []:
                result = self._download(
                    session,
                    attachment.get("url"),
                    workspace / "context" / "attachments",
                    attachment.get("display_name") or attachment.get("filename"),
                    int(attachment.get("size") or 0),
                )
                if result:
                    downloaded.append({"source": "assignment", **result})

            linked_file_ids = sorted(
                set(re.findall(r"/files/(\d+)", assignment.get("description") or ""))
            )
            for file_id in linked_file_ids:
                try:
                    linked = client.get_json(f"/api/v1/files/{file_id}")
                    result = self._download(
                        session,
                        linked.get("url"),
                        workspace / "context" / "attachments",
                        linked.get("display_name") or linked.get("filename") or f"file-{file_id}",
                        int(linked.get("size") or 0),
                    )
                    if result:
                        downloaded.append({"source": "assignment_link", "file_id": int(file_id), **result})
                except Exception as exc:
                    downloaded.append(
                        {"source": "assignment_link", "file_id": int(file_id), "skipped": True, "reason": str(exc)}
                    )

            with Store(self.settings.database_path) as store:
                candidates = store.relevant_materials(int(assignment_row["course_id"]))
            selected = select_relevant_materials(assignment, candidates, self.settings.agent_material_limit)
            for material in selected:
                raw = json.loads(material["raw_json"])
                file_id = raw.get("id")
                current = client.get_json(f"/api/v1/files/{file_id}") if file_id else raw
                result = self._download(
                    session,
                    current.get("url") or material.get("url"),
                    workspace / "context" / "materials",
                    current.get("display_name") or current.get("filename") or material.get("title"),
                    int(current.get("size") or 0),
                )
                if result:
                    downloaded.append({"source": "course_material", "item_key": material["item_key"], **result})

        description_text = html_to_text(assignment.get("description"))
        manifest = {
            "assignment": {
                "id": assignment.get("id"),
                "course_id": assignment_row["course_id"],
                "course_name": assignment_row["course_name"],
                "name": assignment.get("name"),
                "description_html": assignment.get("description"),
                "description_text": description_text,
                "due_at": assignment.get("due_at"),
                "points_possible": assignment.get("points_possible"),
                "submission_types": assignment.get("submission_types") or [],
                "allowed_extensions": assignment.get("allowed_extensions") or [],
                "rubric": assignment.get("rubric") or [],
            },
            "downloaded_context": downloaded,
            "safety": {
                "review_only": True,
                "submission_enabled": False,
                "test_mode": test_mode,
                "permanently_non_submittable": test_mode,
                "note": "This workspace cannot submit to eLearning.",
            },
        }
        (workspace / "assignment.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        assignment_md = (
            f"# {assignment.get('name') or 'Assignment'}\n\n"
            f"Course: {assignment_row['course_name']}\n\n"
            f"Due: {assignment.get('due_at') or 'No due date'}\n\n"
            f"## Instructions\n\n{description_text or 'No text description was provided.'}\n"
        )
        (workspace / "ASSIGNMENT.md").write_text(assignment_md, encoding="utf-8")
        return manifest

    def _download(
        self,
        session: ElearningSession,
        url: str | None,
        destination: Path,
        filename: str | None,
        declared_size: int,
    ) -> dict[str, Any] | None:
        if not url:
            return None
        max_bytes = self.settings.agent_max_file_mb * 1024 * 1024
        if declared_size and declared_size > max_bytes:
            return {"name": filename or "file", "skipped": True, "reason": "file_too_large", "size": declared_size}
        response = session.request.get(url, timeout=60_000)
        if not response.ok:
            return {"name": filename or "file", "skipped": True, "reason": f"HTTP {response.status}"}
        body = response.body()
        if len(body) > max_bytes:
            return {"name": filename or "file", "skipped": True, "reason": "file_too_large", "size": len(body)}
        safe_name = _safe_filename(filename or "download")
        target = _unique_path(destination / safe_name)
        target.write_bytes(body)
        return {
            "name": target.name,
            "path": str(target.relative_to(destination.parent.parent)),
            "size": len(body),
            "content_type": response.headers.get("content-type") or mimetypes.guess_type(target.name)[0],
        }

    def _run_codex(self, workspace: Path, manifest: dict[str, Any]) -> tuple[int, Path, list[dict[str, Any]]]:
        codex = shutil.which("codex")
        if not codex:
            raise DraftAgentError("Codex CLI is not installed or not available on PATH")
        final_answer = workspace / "output" / "final-answer.md"
        last_message = workspace / "output" / "agent-last-message.md"
        prompt = """You are a careful assignment draft agent. Work only inside this directory.

Read ASSIGNMENT.md, assignment.json, and every useful file under context/. Produce a reviewable draft, not a submission. Never access eLearning, never upload anything, and never claim the work was submitted.

Requirements:
1. Write the main response to output/final-answer.md.
2. Put any code, calculations, tables, or other deliverable files under output/.
3. State assumptions, uncertainties, and any parts the student must verify.
4. Cite local source filenames when relying on provided course materials.
5. Include a short self-check section for correctness and completeness.
6. Do not fabricate facts that are absent from the supplied context.

The student will review and may edit every artifact before a separate approval step.
"""
        command = [
            codex, "exec", "--ephemeral", "--skip-git-repo-check",
            "--disable", "plugins", "--disable", "apps", "--disable", "browser_use",
            "--disable", "computer_use", "--disable", "image_generation",
            "-c", "mcp_servers={}",
            "-s", "workspace-write", "-C", str(workspace), "--color", "never",
            "-o", str(last_message), "-",
        ]
        env = os.environ.copy()
        for key in ("ELEARNING_USERNAME", "ELEARNING_PASSWORD", "ELEARNING_WEBHOOK_URL"):
            env.pop(key, None)
        images = [
            workspace / item["path"]
            for item in manifest.get("downloaded_context", [])
            if item.get("path") and _is_image(item["path"])
        ]
        for image in images[:5]:
            command[2:2] = ["-i", str(image)]
        result = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=self.settings.agent_timeout_seconds,
        )
        (workspace / "codex.log").write_text(
            result.stdout + "\n--- STDERR ---\n" + result.stderr, encoding="utf-8"
        )
        if not final_answer.exists() and last_message.exists():
            shutil.copy2(last_message, final_answer)
        if not final_answer.exists():
            final_answer.write_text("Codex did not produce a draft. See codex.log.", encoding="utf-8")
        assignment = manifest.get("assignment") or {}
        pdf_path = workspace / "output" / "final-answer.pdf"
        if not pdf_path.exists():
            render_markdown_pdf(
                final_answer,
                pdf_path,
                title=assignment.get("name") or "Assignment Draft",
                course=assignment.get("course_name") or "eLearning",
            )
        submission_pdf = workspace / "output" / "submission-ready.pdf"
        if not submission_pdf.exists():
            render_markdown_pdf(
                final_answer,
                submission_pdf,
                title=assignment.get("name") or "Assignment",
                course=assignment.get("course_name") or "eLearning",
                submission_copy=True,
            )
        artifacts = _list_artifacts(workspace / "output")
        return result.returncode, final_answer, artifacts


def select_relevant_materials(
    assignment: dict[str, Any], materials: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    query = f"{assignment.get('name') or ''} {html_to_text(assignment.get('description'))}"
    query_tokens = _tokens(query)
    scored: list[tuple[int, dict[str, Any]]] = []
    for material in materials:
        text = f"{material.get('title') or ''} {material.get('module_name') or ''}"
        score = len(query_tokens & _tokens(text))
        if score or not scored:
            scored.append((score, material))
    scored.sort(key=lambda item: (-item[0], item[1].get("title") or ""))
    return [item for _, item in scored[: max(0, limit)]]


def _tokens(value: str) -> set[str]:
    words = set(re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", value.lower()))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", value))
    words.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return words


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value).strip(" .")
    return cleaned[:180] or "file"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise DraftAgentError(f"Too many duplicate filenames for {path.name}")


def _is_image(path: str) -> bool:
    return Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _list_artifacts(output_dir: Path) -> list[dict[str, Any]]:
    artifacts = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_file():
            artifacts.append(
                {
                    "path": str(path.relative_to(output_dir.parent)).replace("\\", "/"),
                    "name": path.name,
                    "size": path.stat().st_size,
                    "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                }
            )
    return artifacts
