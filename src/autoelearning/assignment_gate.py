from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


ELIGIBLE = "eligible"


@dataclass(frozen=True)
class GateDecision:
    status: str
    reason: str
    evidence: tuple[str, ...] = ()
    fingerprint: str = ""

    def as_record(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence"] = list(self.evidence)
        return value


def evaluate_assignment(row: dict[str, Any], *, now: datetime | None = None) -> GateDecision:
    """Conservatively decide whether an assignment has enough context for auto drafting.

    A false negative is preferable to starting work on a paper-only hand-in, a late-work
    portal, or an assignment whose actual questions are unavailable.
    """
    raw = _raw_assignment(row)
    title = str(raw.get("name") or row.get("name") or "").strip()
    description_html = str(raw.get("description") or "")
    description = _html_to_text(description_html)
    combined = f"{title}\n{description}".lower()
    fingerprint = _fingerprint(raw, row)
    submission = raw.get("submission") or {}
    state = submission.get("workflow_state") or row.get("submission_state")
    submission_types = set(raw.get("submission_types") or [])

    if state in {"submitted", "graded"}:
        return _decision("completed", "已经提交或评分，无需自动生成", fingerprint)
    matched = _find_keyword(combined, (
        "补交通道", "作业补交", "补交入口", "补交作业", "late submission",
        "make-up submission", "makeup submission", "resubmission portal",
    ))
    if matched:
        return _decision("ignored_makeup", "识别为补交或重交通道", fingerprint, matched)

    matched = _find_keyword(combined, (
        "纸质版", "纸质作业", "提交纸质", "交纸质", "线下提交", "课堂提交",
        "课上提交", "当堂提交", "交到办公室", "hard copy", "paper copy",
        "hand in class", "submit in class", "offline submission",
    ))
    if matched:
        return _decision("ignored_offline", "要求纸质版或线下提交", fingerprint, matched)

    matched = _find_keyword(combined, (
        "无需提交", "不需要提交", "不用提交", "仅供查看", "no submission required",
        "do not submit", "nothing to submit",
    ))
    if matched:
        return _decision("ignored_no_submission", "说明明确表示无需线上提交", fingerprint, matched)

    if raw.get("locked_for_user"):
        return _decision("ignored_locked", "作业已被课程锁定", fingerprint)
    due_at = _parse_time(raw.get("due_at") or row.get("due_at"))
    current = now or datetime.now(timezone.utc)
    if due_at and due_at < current and not raw.get("has_overrides"):
        return _decision("ignored_expired", "截止时间已过，不自动处理", fingerprint)

    supported = submission_types & {"online_text_entry", "online_upload"}
    if not supported:
        if "online_quiz" in submission_types:
            return _decision("unsupported_quiz", "在线测验题目不在作业说明中，暂不自动处理", fingerprint)
        return _decision("ignored_offline", "eLearning 未提供可支持的文本或文件提交方式", fingerprint)

    attachments = raw.get("attachments") or []
    linked_file_ids = re.findall(r"/files/(\d+)", description_html)
    attachment_names = [
        str(item.get("display_name") or item.get("filename") or item.get("id"))
        for item in attachments if isinstance(item, dict)
    ]
    if attachment_names or linked_file_ids:
        evidence = tuple(f"题目附件：{name}" for name in attachment_names[:3])
        if linked_file_ids:
            evidence += (f"说明中包含 {len(set(linked_file_ids))} 个课程文件链接",)
        return _decision("eligible", "存在可读取的题目附件，可生成审查草稿", fingerprint, *evidence)

    if not description:
        return _decision("needs_context", "没有题面、说明或题目附件", fingerprint)

    reference_only = bool(re.search(
        r"(?:题号|习题|课本|教材|chapter|section|exercise|problem(?:s)?)\s*[:：#]?\s*"
        r"[\d\s,，、.()（）\-–—]+$",
        description,
        re.IGNORECASE,
    ))
    action_words = re.search(
        r"(?:证明|计算|求解|推导|回答|完成|编写|实现|分析|讨论|解释|翻译|写作|"
        r"prove|show|compute|calculate|solve|derive|answer|implement|write|explain|analy[sz]e)",
        description,
        re.IGNORECASE,
    )
    question_structure = bool(re.search(r"(?:^|\n)\s*(?:\d+[.)、]|[(（][a-zA-Z\d]+[)）])", description))
    if reference_only or (len(description) < 80 and not action_words and not question_structure):
        return _decision("needs_context", "只识别到题号或简略引用，缺少实际题目内容", fingerprint)
    if action_words or question_structure or ("?" in description and len(description) >= 50):
        return _decision("eligible", "作业说明包含可执行的完整任务", fingerprint, "已识别题面或任务要求")
    return _decision("needs_context", "说明不足以确认 Agent 能独立完成", fingerprint)


def _decision(status: str, reason: str, fingerprint: str, *evidence: str) -> GateDecision:
    return GateDecision(status, reason, tuple(value for value in evidence if value), fingerprint)


def _raw_assignment(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("raw_json")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return dict(row)
    if isinstance(value, dict):
        return value
    return dict(row)


def _html_to_text(value: str) -> str:
    value = re.sub(r"<(?:br|/p|/div|/li|/tr|/h\d)\b[^>]*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", value)).strip()


def _find_keyword(value: str, keywords: tuple[str, ...]) -> str | None:
    return next((keyword for keyword in keywords if keyword in value), None)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _fingerprint(raw: dict[str, Any], row: dict[str, Any]) -> str:
    relevant = {
        "name": raw.get("name") or row.get("name"),
        "description": raw.get("description"),
        "due_at": raw.get("due_at") or row.get("due_at"),
        "submission_types": raw.get("submission_types"),
        "attachments": raw.get("attachments"),
        "locked_for_user": raw.get("locked_for_user"),
        "submission": raw.get("submission"),
    }
    encoded = json.dumps(relevant, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
