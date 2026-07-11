from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    base_url: str
    username: str | None
    password: str | None
    headless: bool
    data_dir: Path
    remind_days: int
    webhook_url: str | None
    browser_executable: str | None
    submission_enabled: bool
    agent_timeout_seconds: int
    agent_material_limit: int
    agent_max_file_mb: int

    @property
    def profile_dir(self) -> Path:
        return self.data_dir / "browser-profile"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "elearning.sqlite3"

    @property
    def storage_state_path(self) -> Path:
        return self.data_dir / "browser-state.json"

    @property
    def agent_jobs_dir(self) -> Path:
        path = self.data_dir / "agent-jobs"
        path.mkdir(parents=True, exist_ok=True)
        return path


def load_settings() -> Settings:
    load_dotenv()
    data_dir = Path(os.getenv("ELEARNING_DATA_DIR", ".data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        base_url=os.getenv("ELEARNING_BASE_URL", "https://elearning.fudan.edu.cn").rstrip("/"),
        username=os.getenv("ELEARNING_USERNAME") or None,
        password=os.getenv("ELEARNING_PASSWORD") or None,
        headless=_as_bool(os.getenv("ELEARNING_HEADLESS"), True),
        data_dir=data_dir,
        remind_days=int(os.getenv("ELEARNING_REMIND_DAYS", "7")),
        webhook_url=os.getenv("ELEARNING_WEBHOOK_URL") or None,
        browser_executable=os.getenv("ELEARNING_BROWSER_EXECUTABLE") or _find_browser(),
        submission_enabled=_as_bool(os.getenv("ELEARNING_SUBMISSION_ENABLED"), False),
        agent_timeout_seconds=int(os.getenv("ELEARNING_AGENT_TIMEOUT_SECONDS", "1800")),
        agent_material_limit=int(os.getenv("ELEARNING_AGENT_MATERIAL_LIMIT", "6")),
        agent_max_file_mb=int(os.getenv("ELEARNING_AGENT_MAX_FILE_MB", "25")),
    )


def _find_browser() -> str | None:
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ]
    return str(next((path for path in candidates if path.exists()), "")) or None
