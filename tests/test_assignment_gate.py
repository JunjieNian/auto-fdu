from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoelearning.assignment_gate import ELIGIBLE, evaluate_assignment
from autoelearning.desktop_app import _apply_assignment_gate
from autoelearning.storage import Store


NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)


def assignment(**overrides):
    value = {
        "id": 10,
        "name": "Homework 12",
        "description": "Prove that the stated sequence converges and compute its limit.",
        "submission_types": ["online_upload"],
        "submission": {"workflow_state": "unsubmitted"},
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    ("value", "status"),
    [
        (assignment(description="请打印后提交纸质版。"), "ignored_offline"),
        (assignment(name="作业补交通道", description="Upload here."), "ignored_makeup"),
        (assignment(description="教材习题：3.1, 3.4, 3.8"), "needs_context"),
        (assignment(description=""), "needs_context"),
        (assignment(submission_types=["on_paper"]), "ignored_offline"),
        (assignment(submission_types=["online_quiz"]), "unsupported_quiz"),
        (assignment(submission={"workflow_state": "graded"}), "completed"),
    ],
)
def test_gate_rejects_assignments_that_should_not_be_auto_drafted(value, status):
    assert evaluate_assignment(value, now=NOW).status == status


def test_gate_accepts_a_complete_text_prompt():
    decision = evaluate_assignment(assignment(), now=NOW)
    assert decision.status == ELIGIBLE
    assert "完整任务" in decision.reason


def test_gate_accepts_an_assignment_file_even_when_description_is_empty():
    decision = evaluate_assignment(
        assignment(description="", attachments=[{"id": 8, "filename": "A12.pdf"}]),
        now=NOW,
    )
    assert decision.status == ELIGIBLE
    assert decision.evidence == ("题目附件：A12.pdf",)


def test_storage_resets_gate_only_when_assignment_content_changes(tmp_path: Path):
    with Store(tmp_path / "gate.sqlite3") as store:
        store.upsert_courses([{"id": 1, "name": "Course"}])
        original = assignment()
        store.upsert_assignments(1, [original])
        decision = evaluate_assignment(original, now=NOW)
        store.update_assignment_gate(
            10, status=decision.status, reason=decision.reason,
            evidence=list(decision.evidence), fingerprint=decision.fingerprint,
        )
        store.upsert_assignments(1, [original])
        assert store.get_assignment(10)["gate_status"] == ELIGIBLE

        changed = assignment(description="教材习题：3.1, 3.4")
        store.upsert_assignments(1, [changed])
        assert store.get_assignment(10)["gate_status"] == "pending"


def test_auto_queue_only_includes_new_eligible_assignments(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("autoelearning.desktop_app.shutil.which", lambda _: "codex")
    with Store(tmp_path / "queue.sqlite3") as store:
        store.upsert_courses([{"id": 1, "name": "Course"}])
        old_assignment = assignment(id=10)
        new_assignment = assignment(id=11, name="Homework 13")
        store.upsert_assignments(1, [old_assignment, new_assignment])

        summary, job_ids = _apply_assignment_gate(store, {11})

        assert summary["auto_queued"] == 1
        assert len(job_ids) == 1
        assert not store.has_agent_job(10)
        assert store.has_agent_job(11)
