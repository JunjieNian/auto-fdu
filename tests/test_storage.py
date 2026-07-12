from pathlib import Path

import pytest

from autoelearning.config import load_settings
from autoelearning.storage import Store
from autoelearning.submission import (
    ApprovalError,
    SubmissionLockedError,
    approve_job,
    submit_approved_job,
)


def test_upsert_and_counts(tmp_path: Path):
    with Store(tmp_path / "test.sqlite3") as store:
        store.upsert_courses([{"id": 1, "name": "Course", "course_code": "C1"}])
        store.upsert_assignments(1, [{"id": 2, "name": "Homework", "due_at": None}])
        assert store.counts() == {"courses": 1, "assignments": 1, "announcements": 0, "materials": 0}


def test_agent_approval_requires_exact_human_confirmation(tmp_path: Path):
    workspace = tmp_path / "job"
    output = workspace / "output"
    output.mkdir(parents=True)
    draft = output / "final-answer.md"
    draft.write_text("review me", encoding="utf-8")
    database = tmp_path / "test.sqlite3"
    with Store(database) as store:
        store.upsert_courses([{"id": 1, "name": "Course", "course_code": "C1"}])
        store.upsert_assignments(
            1,
            [{
                "id": 2,
                "name": "Homework",
                "due_at": None,
                "submission_types": ["online_text_entry", "online_upload"],
            }],
        )
        job_id = store.create_agent_job(2, 1)
        store.update_agent_job(
            job_id,
            status="draft_ready",
            workspace=str(workspace),
            draft_path=str(draft),
            artifacts_json=[{
                "path": "output/final-answer.md", "name": draft.name,
                "size": draft.stat().st_size, "content_type": "text/markdown",
            }],
        )
        with pytest.raises(ApprovalError):
            approve_job(
                store,
                job_id,
                reviewed=True,
                confirmation="wrong title",
                submission_type="online_text_entry",
                artifact_paths=[],
            )
        approval = approve_job(
            store,
            job_id,
            reviewed=True,
            confirmation="Homework",
            submission_type="online_text_entry",
            artifact_paths=[],
        )
        assert approval["approval_token"]
        assert store.get_agent_job(job_id)["status"] == "approved"


def test_global_submission_lock_stops_before_canvas(tmp_path: Path):
    settings = load_settings()
    settings = settings.__class__(
        **{**settings.__dict__, "data_dir": tmp_path, "submission_enabled": False}
    )
    with pytest.raises(SubmissionLockedError):
        submit_approved_job(settings, 999, "unused", username=None, password=None)


def test_completed_assignment_test_mode_can_never_be_approved(tmp_path: Path):
    workspace = tmp_path / "job"
    output = workspace / "output"
    output.mkdir(parents=True)
    draft = output / "final-answer.md"
    draft.write_text("test draft", encoding="utf-8")
    with Store(tmp_path / "test.sqlite3") as store:
        store.upsert_courses([{"id": 1, "name": "Course", "course_code": "C1"}])
        store.upsert_assignments(1, [{
            "id": 2, "name": "Completed homework", "submission_state": "graded",
            "submission_types": ["online_upload"],
        }])
        job_id = store.create_agent_job(2, 1, test_mode=True)
        store.update_agent_job(
            job_id, status="draft_ready", workspace=str(workspace), draft_path=str(draft),
            artifacts_json=[{"path": "output/final-answer.md", "name": draft.name, "size": 10}],
        )
        with pytest.raises(ApprovalError, match="永久禁止"):
            approve_job(
                store, job_id, reviewed=True, confirmation="Completed homework",
                submission_type="online_upload", artifact_paths=["output/final-answer.md"],
            )


def test_agent_messages_persist_in_order(tmp_path: Path):
    with Store(tmp_path / "messages.sqlite3") as store:
        store.upsert_courses([{"id": 1, "name": "Course"}])
        store.upsert_assignments(1, [{"id": 2, "name": "Homework"}])
        job_id = store.create_agent_job(2, 1)
        store.add_agent_message(job_id, "user", "Add a missing proof.")
        store.add_agent_message(job_id, "assistant", "The proof and PDF were updated.")
        assert [item["role"] for item in store.list_agent_messages(job_id)] == [
            "user", "assistant",
        ]


def test_approval_must_include_current_submission_pdf(tmp_path: Path):
    workspace = tmp_path / "job"
    output = workspace / "output"
    output.mkdir(parents=True)
    draft = output / "final-answer.md"
    draft.write_text("review me", encoding="utf-8")
    wrong = output / "final-answer.pdf"
    wrong.write_bytes(b"review")
    expected = output / "submission-ready.pdf"
    expected.write_bytes(b"submission")
    with Store(tmp_path / "approval.sqlite3") as store:
        store.upsert_courses([{"id": 1, "name": "Course"}])
        store.upsert_assignments(1, [{
            "id": 2, "name": "Homework", "submission_types": ["online_upload"],
        }])
        job_id = store.create_agent_job(2, 1)
        store.update_agent_job(
            job_id, status="draft_ready", workspace=str(workspace), draft_path=str(draft),
            submission_artifact_path="output/submission-ready.pdf",
            artifacts_json=[
                {"path": "output/final-answer.pdf", "name": wrong.name, "size": 6},
                {"path": "output/submission-ready.pdf", "name": expected.name, "size": 10},
            ],
        )
        with pytest.raises(ApprovalError, match="当前显示"):
            approve_job(
                store, job_id, reviewed=True, confirmation="Homework",
                submission_type="online_upload", artifact_paths=["output/final-answer.pdf"],
            )
