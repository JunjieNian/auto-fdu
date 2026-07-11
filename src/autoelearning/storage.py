from __future__ import annotations

import json
import sqlite3
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS courses (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, course_code TEXT, raw_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assignments (
  id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL, name TEXT NOT NULL,
  due_at TEXT, html_url TEXT, submission_state TEXT, raw_json TEXT NOT NULL,
  updated_at TEXT NOT NULL, first_seen_at TEXT,
  gate_status TEXT NOT NULL DEFAULT 'pending', gate_reason TEXT,
  gate_evidence_json TEXT, gate_fingerprint TEXT, gate_evaluated_at TEXT
);
CREATE TABLE IF NOT EXISTS announcements (
  id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL, title TEXT NOT NULL,
  posted_at TEXT, html_url TEXT, raw_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS materials (
  item_key TEXT PRIMARY KEY, course_id INTEGER NOT NULL, kind TEXT NOT NULL,
  title TEXT NOT NULL, url TEXT, module_name TEXT, raw_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL, summary_json TEXT
);
CREATE TABLE IF NOT EXISTS agent_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  assignment_id INTEGER NOT NULL,
  course_id INTEGER NOT NULL,
  status TEXT NOT NULL,
  test_mode INTEGER NOT NULL DEFAULT 0,
  workspace TEXT,
  draft_path TEXT,
  manifest_json TEXT,
  artifacts_json TEXT,
  approved_artifacts_json TEXT,
  submission_type TEXT,
  review_notes TEXT,
  approved_at TEXT,
  approval_token TEXT,
  approval_expires_at TEXT,
  submitted_at TEXT,
  submission_response_json TEXT,
  error TEXT,
  codex_exit_code INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_jobs_assignment ON agent_jobs(assignment_id, created_at DESC);
"""


class Store(AbstractContextManager["Store"]):
    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(agent_jobs)")
        }
        if "approved_artifacts_json" not in columns:
            self.connection.execute(
                "ALTER TABLE agent_jobs ADD COLUMN approved_artifacts_json TEXT"
            )
            self.connection.commit()
        if "test_mode" not in columns:
            self.connection.execute(
                "ALTER TABLE agent_jobs ADD COLUMN test_mode INTEGER NOT NULL DEFAULT 0"
            )
            self.connection.commit()
        assignment_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(assignments)")
        }
        migrations = {
            "first_seen_at": "TEXT",
            "gate_status": "TEXT NOT NULL DEFAULT 'pending'",
            "gate_reason": "TEXT",
            "gate_evidence_json": "TEXT",
            "gate_fingerprint": "TEXT",
            "gate_evaluated_at": "TEXT",
        }
        for name, definition in migrations.items():
            if name not in assignment_columns:
                self.connection.execute(f"ALTER TABLE assignments ADD COLUMN {name} {definition}")
        self.connection.execute(
            "UPDATE assignments SET first_seen_at=COALESCE(first_seen_at,updated_at)"
        )
        self.connection.commit()

    def upsert_courses(self, rows: Iterable[dict[str, Any]]) -> None:
        now = _now()
        self.connection.executemany(
            """INSERT INTO courses VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, course_code=excluded.course_code,
            raw_json=excluded.raw_json, updated_at=excluded.updated_at""",
            [(r["id"], r.get("name") or r.get("course_code") or str(r["id"]), r.get("course_code"), _json(r), now) for r in rows],
        )
        self.connection.commit()

    def upsert_assignments(self, course_id: int, rows: Iterable[dict[str, Any]]) -> None:
        now = _now()
        values = []
        for row in rows:
            submission = row.get("submission") or {}
            values.append((row["id"], course_id, row.get("name") or str(row["id"]), row.get("due_at"), row.get("html_url"), submission.get("workflow_state"), _json(row), now, now))
        self.connection.executemany(
            """INSERT INTO assignments(
            id,course_id,name,due_at,html_url,submission_state,raw_json,updated_at,first_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, due_at=excluded.due_at,
            html_url=excluded.html_url, submission_state=excluded.submission_state,
            gate_status=CASE WHEN assignments.raw_json<>excluded.raw_json THEN 'pending' ELSE assignments.gate_status END,
            gate_reason=CASE WHEN assignments.raw_json<>excluded.raw_json THEN NULL ELSE assignments.gate_reason END,
            gate_evidence_json=CASE WHEN assignments.raw_json<>excluded.raw_json THEN NULL ELSE assignments.gate_evidence_json END,
            gate_fingerprint=CASE WHEN assignments.raw_json<>excluded.raw_json THEN NULL ELSE assignments.gate_fingerprint END,
            gate_evaluated_at=CASE WHEN assignments.raw_json<>excluded.raw_json THEN NULL ELSE assignments.gate_evaluated_at END,
            raw_json=excluded.raw_json, updated_at=excluded.updated_at""", values,
        )
        self.connection.commit()

    def upsert_announcements(self, course_id: int, rows: Iterable[dict[str, Any]]) -> None:
        now = _now()
        self.connection.executemany(
            """INSERT INTO announcements VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET title=excluded.title, posted_at=excluded.posted_at,
            html_url=excluded.html_url, raw_json=excluded.raw_json, updated_at=excluded.updated_at""",
            [(r["id"], course_id, r.get("title") or str(r["id"]), r.get("posted_at"), r.get("html_url"), _json(r), now) for r in rows],
        )
        self.connection.commit()

    def upsert_materials(self, course_id: int, modules: list[dict[str, Any]], files: list[dict[str, Any]]) -> None:
        now = _now()
        values: list[tuple[Any, ...]] = []
        for module in modules:
            for item in module.get("items") or []:
                values.append((f"module:{course_id}:{item.get('id')}", course_id, item.get("type") or "module_item", item.get("title") or str(item.get("id")), item.get("html_url") or item.get("external_url"), module.get("name"), _json(item), now))
        for item in files:
            values.append((f"file:{course_id}:{item.get('id')}", course_id, "File", item.get("display_name") or item.get("filename") or str(item.get("id")), item.get("url"), None, _json(item), now))
        self.connection.executemany(
            """INSERT INTO materials VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_key) DO UPDATE SET title=excluded.title, url=excluded.url,
            module_name=excluded.module_name, raw_json=excluded.raw_json, updated_at=excluded.updated_at""", values,
        )
        self.connection.commit()

    def start_run(self) -> int:
        cursor = self.connection.execute("INSERT INTO sync_runs(started_at,status) VALUES (?,?)", (_now(), "running"))
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, summary: dict[str, Any]) -> None:
        self.connection.execute("UPDATE sync_runs SET finished_at=?,status=?,summary_json=? WHERE id=?", (_now(), status, _json(summary), run_id))
        self.connection.commit()

    def upcoming_assignments(self, days: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT a.*, c.name AS course_name FROM assignments a JOIN courses c ON c.id=a.course_id
            WHERE a.due_at IS NOT NULL AND datetime(a.due_at) >= datetime('now')
            AND datetime(a.due_at) <= datetime('now', ?) AND COALESCE(a.submission_state,'') NOT IN ('submitted','graded')
            ORDER BY datetime(a.due_at)""", (f"+{days} days",),
        ).fetchall()
        return [dict(row) for row in rows]

    def counts(self) -> dict[str, int]:
        return {name: self.connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in ("courses", "assignments", "announcements", "materials")}

    def get_assignment(self, assignment_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT a.*, c.name AS course_name, c.course_code FROM assignments a
            JOIN courses c ON c.id=a.course_id WHERE a.id=?""",
            (assignment_id,),
        ).fetchone()
        return dict(row) if row else None

    def assignment_ids(self) -> set[int]:
        return {int(row[0]) for row in self.connection.execute("SELECT id FROM assignments")}

    def assignments_for_gate(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT a.*,c.name AS course_name,c.course_code FROM assignments a
            JOIN courses c ON c.id=a.course_id ORDER BY a.id"""
        ).fetchall()
        return [dict(row) for row in rows]

    def update_assignment_gate(
        self, assignment_id: int, *, status: str, reason: str,
        evidence: list[str], fingerprint: str,
    ) -> None:
        self.connection.execute(
            """UPDATE assignments SET gate_status=?,gate_reason=?,gate_evidence_json=?,
            gate_fingerprint=?,gate_evaluated_at=? WHERE id=?""",
            (status, reason, _json(evidence), fingerprint, _now(), assignment_id),
        )
        self.connection.commit()

    def has_agent_job(self, assignment_id: int) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM agent_jobs WHERE assignment_id=? LIMIT 1", (assignment_id,)
        ).fetchone()
        return row is not None

    def queued_agent_job_ids(self) -> list[int]:
        return [
            int(row[0]) for row in self.connection.execute(
                "SELECT id FROM agent_jobs WHERE status='queued' ORDER BY id"
            ).fetchall()
        ]

    def relevant_materials(self, course_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT item_key,course_id,kind,title,url,module_name,raw_json
            FROM materials WHERE course_id=? ORDER BY title""",
            (course_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def create_agent_job(self, assignment_id: int, course_id: int, *, test_mode: bool = False) -> int:
        now = _now()
        cursor = self.connection.execute(
            """INSERT INTO agent_jobs(assignment_id,course_id,status,test_mode,created_at,updated_at)
            VALUES (?,?,?,?,?,?)""",
            (assignment_id, course_id, "queued", int(test_mode), now, now),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def update_agent_job(self, job_id: int, **fields: Any) -> None:
        allowed = {
            "status", "workspace", "draft_path", "manifest_json", "artifacts_json",
            "approved_artifacts_json",
            "submission_type", "review_notes", "approved_at", "approval_token",
            "approval_expires_at", "submitted_at", "submission_response_json", "error",
            "codex_exit_code",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unsupported agent job fields: {sorted(unknown)}")
        fields["updated_at"] = _now()
        values = [(_json(value) if key.endswith("_json") and not isinstance(value, str) else value) for key, value in fields.items()]
        assignments = ",".join(f"{key}=?" for key in fields)
        self.connection.execute(
            f"UPDATE agent_jobs SET {assignments} WHERE id=?", (*values, job_id)
        )
        self.connection.commit()

    def get_agent_job(self, job_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT j.*, a.name AS assignment_name, a.due_at, a.html_url,
            a.submission_state, a.raw_json AS assignment_raw_json,
            c.name AS course_name, c.course_code
            FROM agent_jobs j JOIN assignments a ON a.id=j.assignment_id
            JOIN courses c ON c.id=j.course_id WHERE j.id=?""",
            (job_id,),
        ).fetchone()
        return _decode_job(dict(row)) if row else None

    def list_agent_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT j.*, a.name AS assignment_name, a.due_at, a.submission_state,
            c.name AS course_name, c.course_code
            FROM agent_jobs j JOIN assignments a ON a.id=j.assignment_id
            JOIN courses c ON c.id=j.course_id ORDER BY j.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [_decode_job(dict(row)) for row in rows]

    def __exit__(self, exc_type, exc, tb) -> None:
        self.connection.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode_job(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("manifest_json", "artifacts_json", "approved_artifacts_json", "submission_response_json"):
        value = row.get(key)
        if value:
            try:
                row[key[:-5]] = json.loads(value)
            except json.JSONDecodeError:
                row[key[:-5]] = None
        else:
            row[key[:-5]] = None
    return row
