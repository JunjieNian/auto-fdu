from __future__ import annotations

from datetime import datetime, timedelta, timezone
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from .auth import ElearningSession


class CanvasApiError(RuntimeError):
    pass


class CanvasClient:
    def __init__(self, session: ElearningSession):
        self.session = session
        self.base_url = session.settings.base_url

    def get_json(self, path_or_url: str, params: list[tuple[str, str]] | None = None) -> Any:
        url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}{path_or_url}"
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        response = self.session.request.get(url, timeout=60_000)
        if not response.ok:
            raise CanvasApiError(f"GET {urlparse(url).path} failed with HTTP {response.status}")
        return response.json()

    def get_all(self, path: str, params: list[tuple[str, str]] | None = None) -> list[dict[str, Any]]:
        query = list(params or []) + [("per_page", "100")]
        url = f"{self.base_url}{path}?{urlencode(query)}"
        result: list[dict[str, Any]] = []
        while url:
            response = self.session.request.get(url, timeout=60_000)
            if not response.ok:
                raise CanvasApiError(f"GET {urlparse(url).path} failed with HTTP {response.status}")
            payload = response.json()
            if not isinstance(payload, list):
                raise CanvasApiError(f"Expected a list from {urlparse(url).path}")
            result.extend(payload)
            url = _next_link(response.headers.get("link", ""))
        return result

    def courses(self) -> list[dict[str, Any]]:
        return self.get_all(
            "/api/v1/courses",
            [("enrollment_state", "active"), ("include[]", "term")],
        )

    def assignments(self, course_id: int) -> list[dict[str, Any]]:
        return self.get_all(
            f"/api/v1/courses/{course_id}/assignments",
            [("include[]", "submission"), ("order_by", "due_at")],
        )

    def assignment(self, course_id: int, assignment_id: int) -> dict[str, Any]:
        return self.get_json(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}",
            [("include[]", "submission")],
        )

    def announcements(self, course_id: int) -> list[dict[str, Any]]:
        return self.get_all(
            f"/api/v1/courses/{course_id}/discussion_topics",
            [("only_announcements", "true")],
        )

    def modules(self, course_id: int) -> list[dict[str, Any]]:
        return self.get_all(
            f"/api/v1/courses/{course_id}/modules", [("include[]", "items")]
        )

    def files(self, course_id: int) -> list[dict[str, Any]]:
        return self.get_all(f"/api/v1/courses/{course_id}/files")

    def planner_items(self, days_back: int = 180, days_forward: int = 180) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        return self.get_all(
            "/api/v1/planner/items",
            [
                ("start_date", (now - timedelta(days=days_back)).isoformat()),
                ("end_date", (now + timedelta(days=days_forward)).isoformat()),
            ],
        )

    def upload_submission_file(
        self, course_id: int, assignment_id: int, path: Path
    ) -> dict[str, Any]:
        """Upload a reviewed file to Canvas without submitting the assignment."""
        first = self.session.request.post(
            f"{self.base_url}/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/self/files",
            form={
                "name": path.name,
                "size": str(path.stat().st_size),
                "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            },
            headers=self.session.csrf_headers(),
            timeout=60_000,
        )
        if not first.ok:
            raise CanvasApiError(f"Preparing upload failed with HTTP {first.status}")
        upload = first.json()
        multipart: dict[str, Any] = dict(upload.get("upload_params") or {})
        multipart["file"] = {
            "name": path.name,
            "mimeType": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "buffer": path.read_bytes(),
        }
        second = self.session.request.post(
            upload["upload_url"], multipart=multipart, timeout=120_000
        )
        if not second.ok:
            raise CanvasApiError(f"Uploading {path.name} failed with HTTP {second.status}")
        return second.json()

    def submit_assignment(
        self,
        course_id: int,
        assignment_id: int,
        submission_type: str,
        *,
        body: str | None = None,
        file_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        form: list[tuple[str, str]] = [("submission[submission_type]", submission_type)]
        if submission_type == "online_text_entry":
            form.append(("submission[body]", body or ""))
        elif submission_type == "online_upload":
            form.extend(("submission[file_ids][]", str(file_id)) for file_id in (file_ids or []))
        else:
            raise CanvasApiError(f"Unsupported submission type: {submission_type}")
        response = self.session.request.post(
            f"{self.base_url}/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions",
            data=urlencode(form),
            headers={"Content-Type": "application/x-www-form-urlencoded", **self.session.csrf_headers()},
            timeout=60_000,
        )
        if not response.ok:
            raise CanvasApiError(f"Submitting assignment failed with HTTP {response.status}")
        return response.json()


def _next_link(link_header: str) -> str | None:
    for chunk in link_header.split(","):
        parts = [part.strip() for part in chunk.split(";")]
        if len(parts) > 1 and parts[1] == 'rel="next"':
            return parts[0].strip("<>")
    return None
