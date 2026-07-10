from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .canvas import CanvasClient
from .storage import Store


@dataclass
class SyncSummary:
    courses: int = 0
    assignments: int = 0
    announcements: int = 0
    materials: int = 0
    course_errors: int = 0


def sync_all(client: CanvasClient, store: Store) -> tuple[SyncSummary, list[str]]:
    summary = SyncSummary()
    errors: list[str] = []
    courses = client.courses()
    store.upsert_courses(courses)
    summary.courses = len(courses)

    for course in courses:
        course_id = int(course["id"])
        try:
            assignments = client.assignments(course_id)
            announcements = client.announcements(course_id)
            modules = client.modules(course_id)
            files = client.files(course_id)
            store.upsert_assignments(course_id, assignments)
            store.upsert_announcements(course_id, announcements)
            store.upsert_materials(course_id, modules, files)
            summary.assignments += len(assignments)
            summary.announcements += len(announcements)
            summary.materials += sum(len(module.get("items") or []) for module in modules) + len(files)
        except Exception as exc:
            summary.course_errors += 1
            errors.append(f"course {course_id}: {exc}")
    return summary, errors


class AssignmentDraftProvider:
    """Extension point for a future Codex-assisted draft workflow.

    Deliberately produces drafts only. Submission stays a separate, human-approved step.
    """

    def create_draft(self, assignment: dict[str, Any], materials: list[dict[str, Any]]) -> Any:
        raise NotImplementedError


class SubmissionProvider:
    """Extension point for uploading an explicitly approved artifact."""

    def submit(self, assignment_id: int, artifact_path: str) -> Any:
        raise NotImplementedError

