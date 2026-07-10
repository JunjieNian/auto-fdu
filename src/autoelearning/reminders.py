from __future__ import annotations

import json
from urllib.request import Request, urlopen


def format_reminders(assignments: list[dict]) -> str:
    if not assignments:
        return "未来提醒窗口内没有未提交作业。"
    lines = ["eLearning 作业提醒："]
    for item in assignments:
        due = item.get("due_at") or "无截止时间"
        lines.append(f"- [{item['course_name']}] {item['name']}（截止：{due}）")
    return "\n".join(lines)


def send_webhook(url: str, message: str) -> None:
    body = json.dumps({"text": message}, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=15) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Webhook returned HTTP {response.status}")
