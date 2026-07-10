from __future__ import annotations

import json
import logging
import socket
import sqlite3
import threading
import time
import webbrowser
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from flask import Flask, jsonify, render_template, request

from .auth import AuthenticationError, ElearningSession
from .canvas import CanvasClient
from .config import Settings, load_settings
from .storage import Store
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
    if state.syncing:
        return _error("同步正在进行中，请稍候。", 409)
    local_settings = replace(settings, username=state.username, password=state.password)
    with state.lock:
        state.syncing = True
        try:
            with Store(local_settings.database_path) as store:
                run_id = store.start_run()
                try:
                    with ElearningSession(local_settings) as session:
                        client = CanvasClient(session)
                        user = client.get_json("/api/v1/users/self")
                        summary, errors = sync_all(client, store)
                    state.user = user
                    result = {**summary.__dict__, "errors": errors}
                    store.finish_run(run_id, "partial" if errors else "success", result)
                    return jsonify({"ok": not errors, **result})
                except Exception as exc:
                    store.finish_run(run_id, "failed", {"error": str(exc)})
                    raise
        except AuthenticationError as exc:
            state.user = None
            return _error(str(exc), 401)
        except Exception as exc:
            app.logger.exception("Sync failed")
            return _error(f"同步失败：{exc}", 500)
        finally:
            state.syncing = False


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
        return jsonify(
            {
                "counts": counts,
                "courses": courses,
                "assignments": assignments,
                "announcements": announcements,
                "materials": materials,
                "last_run": dict(last_run) if last_run else None,
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
    }


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
    log_path = settings.data_dir / "desktop-app.log"
    logging.basicConfig(filename=log_path, level=logging.INFO, encoding="utf-8")
    threading.Timer(1.2, lambda: webbrowser.open(APP_URL)).start()
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()

