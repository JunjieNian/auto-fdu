from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any
from urllib.parse import unquote

from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright

from .config import Settings


class AuthenticationError(RuntimeError):
    pass


class ElearningSession(AbstractContextManager["ElearningSession"]):
    """Persistent browser session used both for CAS login and Canvas API calls."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None

    def __enter__(self) -> "ElearningSession":
        self._playwright = sync_playwright().start()
        launch: dict[str, Any] = {"headless": self.settings.headless}
        if self.settings.browser_executable:
            launch["executable_path"] = self.settings.browser_executable
        self.browser = self._playwright.chromium.launch(**launch)
        context_options: dict[str, Any] = {}
        if self.settings.storage_state_path.exists():
            context_options["storage_state"] = str(self.settings.storage_state_path)
        self.context = self.browser.new_context(**context_options)
        self.ensure_authenticated()
        return self

    def ensure_authenticated(self) -> None:
        assert self.context is not None
        probe = self.context.request.get(
            f"{self.settings.base_url}/api/v1/users/self", timeout=30_000
        )
        if probe.ok:
            try:
                if probe.json().get("id"):
                    return
            except Exception:
                pass
        page = self.context.pages[0] if self.context.pages else self.context.new_page()
        page.goto(f"{self.settings.base_url}/login", wait_until="domcontentloaded", timeout=60_000)
        if "id.fudan.edu.cn" not in page.url:
            return
        if not self.settings.username or not self.settings.password:
            raise AuthenticationError(
                "登录会话已失效，请设置 ELEARNING_USERNAME 和 ELEARNING_PASSWORD。"
            )

        username = page.locator("#login-username")
        username.wait_for(state="visible", timeout=30_000)
        # The CAS SPA replaces its login form once after loading auth methods.
        # Waiting avoids filling the soon-to-be-discarded first render.
        page.wait_for_timeout(4_000)
        page.locator("#login-username").fill(self.settings.username)
        page.locator("#login-password").fill(self.settings.password)

        captcha = page.locator("input[placeholder*='验证码']")
        if captcha.count() and captcha.is_visible():
            raise AuthenticationError("统一身份认证要求验证码，请先用可见浏览器人工登录一次。")

        button = page.locator("button.content_submit")
        if button.count() != 1 or not button.is_enabled():
            raise AuthenticationError("登录按钮不可用，统一身份认证页面结构可能已变化。")
        button.click()
        try:
            page.wait_for_url(f"{self.settings.base_url}/**", timeout=60_000)
        except Exception as exc:
            errors = page.locator("[class*=error], [role=alert]")
            visible = [text.strip() for text in errors.all_inner_texts() if text.strip()]
            detail = visible[0] if visible else "可能需要二次认证或账号信息无效"
            raise AuthenticationError(f"登录失败：{detail}") from exc

        if "id.fudan.edu.cn" in page.url:
            raise AuthenticationError("登录未完成，可能需要二次认证。")

    @property
    def request(self):
        if self.context is None:
            raise RuntimeError("Session is not open")
        return self.context.request

    def csrf_headers(self) -> dict[str, str]:
        """Return Canvas' CSRF header without exposing the token to logs."""
        if self.context is None:
            raise RuntimeError("Session is not open")
        for cookie in self.context.cookies(self.settings.base_url):
            if cookie.get("name") == "_csrf_token":
                return {"X-CSRF-Token": unquote(cookie.get("value") or "")}
        return {}

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.context is not None:
            self.context.storage_state(path=str(self.settings.storage_state_path))
            self.context.close()
        if self.browser is not None:
            self.browser.close()
        if self._playwright is not None:
            self._playwright.stop()
