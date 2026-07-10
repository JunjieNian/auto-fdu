from __future__ import annotations

import argparse
import json
import sys

from .auth import AuthenticationError, ElearningSession
from .canvas import CanvasClient
from .config import load_settings
from .reminders import format_reminders, send_webhook
from .storage import Store
from .workflow import sync_all


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fudan eLearning collector")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-login", help="verify CAS login and Canvas API access")
    sub.add_parser("sync", help="collect courses, assignments, announcements and materials")
    remind = sub.add_parser("remind", help="print upcoming unsubmitted assignments")
    remind.add_argument("--days", type=int)
    sub.add_parser("status", help="show local database counts")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    try:
        if args.command == "check-login":
            with ElearningSession(settings) as session:
                user = CanvasClient(session).get_json("/api/v1/users/self")
                print(json.dumps({"ok": True, "user_id": user.get("id"), "name": user.get("name")}, ensure_ascii=False))
            return 0
        if args.command == "sync":
            with Store(settings.database_path) as store:
                run_id = store.start_run()
                try:
                    with ElearningSession(settings) as session:
                        summary, errors = sync_all(CanvasClient(session), store)
                    result = {**summary.__dict__, "errors": errors, "database": str(settings.database_path)}
                    store.finish_run(run_id, "partial" if errors else "success", result)
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    return 2 if errors else 0
                except Exception as exc:
                    store.finish_run(run_id, "failed", {"error": str(exc)})
                    raise
        if args.command == "remind":
            days = args.days if args.days is not None else settings.remind_days
            with Store(settings.database_path) as store:
                message = format_reminders(store.upcoming_assignments(days))
            print(message)
            if settings.webhook_url:
                send_webhook(settings.webhook_url, message)
            return 0
        if args.command == "status":
            with Store(settings.database_path) as store:
                print(json.dumps(store.counts(), ensure_ascii=False, indent=2))
            return 0
    except AuthenticationError as exc:
        print(f"认证失败：{exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"执行失败：{exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

