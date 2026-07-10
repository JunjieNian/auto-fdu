from pathlib import Path

from autoelearning.storage import Store


def test_upsert_and_counts(tmp_path: Path):
    with Store(tmp_path / "test.sqlite3") as store:
        store.upsert_courses([{"id": 1, "name": "Course", "course_code": "C1"}])
        store.upsert_assignments(1, [{"id": 2, "name": "Homework", "due_at": None}])
        assert store.counts() == {"courses": 1, "assignments": 1, "announcements": 0, "materials": 0}

