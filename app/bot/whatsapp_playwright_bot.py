from __future__ import annotations

import json
import logging
import os
import re
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from app.bot.conversation import ChatState, ConversationManager
from app.config import settings
from app.services.document_api_client import DocumentApiClient


WHATSAPP_WEB_URL = "https://web.whatsapp.com/"
DEFAULT_POLL_SECONDS = 3
LOGGER = logging.getLogger("whatsapp_playwright_bot")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        LOGGER.warning("Invalid integer for %s=%r. Using %s.", name, raw, default)
        return default


def _env_first(names: list[str], default: str = "") -> str:
    for name in names:
        raw = os.getenv(name)
        if raw is not None:
            return raw
    return default


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip())
    return cleaned.strip("_") or "allowed_chat"


def _normalize_phone(value: str) -> str:
    return re.sub(r"\D+", "", value or "")


@dataclass(frozen=True)
class AllowedChatPolicy:
    allowed_chat_name: str = ""
    allowed_phone: str = ""
    allow_groups: bool = False

    @property
    def search_term(self) -> str:
        return self.allowed_chat_name or self.allowed_phone

    def validate(self) -> None:
        if not self.search_term:
            raise RuntimeError("Set WHATSAPP_ALLOWED_CHAT_NAME or WHATSAPP_ALLOWED_PHONE before running the bot.")

    def matches_header(self, header_text: str) -> bool:
        return self.matches_text(header_text)

    def matches_text(self, candidate_text: str) -> bool:
        self.validate()
        lowered_text = candidate_text.lower()
        normalized_text_phone = _normalize_phone(candidate_text)

        if self.allowed_chat_name and self.allowed_chat_name.lower() not in lowered_text:
            return False
        if self.allowed_phone and _normalize_phone(self.allowed_phone) not in normalized_text_phone:
            return False
        if not self.allow_groups and looks_like_group_header(candidate_text):
            return False
        return True


def looks_like_group_header(header_text: str) -> bool:
    lowered = header_text.lower()
    return "participants" in lowered or "participant" in lowered or len(re.findall(r",", header_text)) >= 2


@dataclass(frozen=True)
class BotSettings:
    enabled: bool
    allowed_chat_name: str
    allowed_phone: str
    session_dir: Path
    download_dir: Path
    allow_groups: bool
    api_base_url: str
    poll_interval_seconds: int
    runtime_dir: Path = Path("runtime")
    headless: bool = False
    qr_screenshot_path: Path | None = None

    @classmethod
    def load(cls) -> "BotSettings":
        load_dotenv()
        session_dir = Path(os.getenv("WHATSAPP_SESSION_DIR", settings.whatsapp_session_dir))
        runtime_dir = session_dir.parent if session_dir.parent != Path("") else Path("runtime")
        qr_path = os.getenv("WHATSAPP_QR_SCREENSHOT_PATH", settings.whatsapp_qr_screenshot_path).strip()
        web_enabled = _env_bool(
            "WHATSAPP_WEB_ENABLED",
            _env_bool("WHATSAPP_ENABLED", settings.whatsapp_web_enabled or settings.whatsapp_enabled),
        )
        allow_groups = _env_bool(
            "WHATSAPP_ALLOW_GROUPS",
            _env_bool("WHATSAPP_ALLOW_GROUP", settings.whatsapp_allow_groups or settings.whatsapp_allow_group),
        )
        return cls(
            enabled=web_enabled,
            allowed_chat_name=os.getenv("WHATSAPP_ALLOWED_CHAT_NAME", settings.whatsapp_allowed_chat_name).strip(),
            allowed_phone=os.getenv("WHATSAPP_ALLOWED_PHONE", settings.whatsapp_allowed_phone).strip(),
            session_dir=session_dir,
            download_dir=Path(os.getenv("WHATSAPP_DOWNLOAD_DIR", settings.whatsapp_download_dir)),
            allow_groups=allow_groups,
            api_base_url=_env_first(["API_BASE_URL", "APP_BASE_URL"], settings.api_base_url).strip().rstrip("/"),
            poll_interval_seconds=_env_int(
                "WHATSAPP_POLL_INTERVAL_SECONDS",
                settings.whatsapp_poll_interval_seconds or DEFAULT_POLL_SECONDS,
            ),
            runtime_dir=runtime_dir,
            headless=_env_bool("WHATSAPP_HEADLESS", settings.whatsapp_headless),
            qr_screenshot_path=Path(qr_path) if qr_path else runtime_dir / "whatsapp-qr-login.png",
        )

    @property
    def allowed_search_term(self) -> str:
        return self.allowed_chat_name or self.allowed_phone

    @property
    def allowed_policy(self) -> AllowedChatPolicy:
        return AllowedChatPolicy(
            allowed_chat_name=self.allowed_chat_name,
            allowed_phone=self.allowed_phone,
            allow_groups=self.allow_groups,
        )

    def validate(self) -> None:
        if not self.enabled:
            raise RuntimeError("WHATSAPP_WEB_ENABLED is false. Set WHATSAPP_WEB_ENABLED=true to run the bot.")
        self.allowed_policy.validate()
        if not self.api_base_url:
            raise RuntimeError("API_BASE_URL is required.")


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> ChatState:
        if not self.path.exists():
            return ChatState()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return ChatState.from_dict(data)
        except Exception as exc:
            LOGGER.warning("Could not read state file %s: %s", self.path, exc)
            return ChatState()

    def save(self, state: ChatState) -> None:
        self.path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


class WhatsAppPlaywrightBot:
    def __init__(self, config: BotSettings):
        config.validate()
        self.config = config
        self.config.session_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir = self.config.download_dir
        self.generated_dir = self.config.runtime_dir / "whatsapp-generated"
        state_path = self.config.runtime_dir / f"whatsapp-state-{_slug(self.config.allowed_search_term)}.json"
        self.state_store = StateStore(state_path)
        self.state = self.state_store.load()
        self.api = DocumentApiClient(self.config.api_base_url, self.generated_dir)
        self.conversation = ConversationManager(self.api)
        self.page: Page | None = None

    def run(self) -> None:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.config.session_dir),
                headless=self.config.headless,
                accept_downloads=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            try:
                self.page = context.pages[0] if context.pages else context.new_page()
                self._open_whatsapp()
                self._open_allowed_chat()
                self._send_text(self.conversation.start_message())
                self._monitor_loop(context)
            finally:
                self.state_store.save(self.state)
                context.close()

    def _open_whatsapp(self) -> None:
        page = self._page
        page.goto(WHATSAPP_WEB_URL, wait_until="domcontentloaded")
        LOGGER.info("Opening WhatsApp Web. Scan the QR code if this is the first launch.")
        try:
            page.wait_for_selector("div[role='textbox'], canvas", timeout=120_000)
        except PlaywrightTimeoutError as exc:
            raise RuntimeError("WhatsApp Web did not load. Check network access and try again.") from exc
        self._wait_until_logged_in()

    def _wait_until_logged_in(self) -> None:
        page = self._page
        while True:
            if self._count("#pane-side, div[aria-label='Chat list'], [aria-label='Chat list']") > 0:
                LOGGER.info("WhatsApp Web session is ready.")
                return
            self._save_qr_screenshot()
            LOGGER.info("Waiting for WhatsApp QR scan/login...")
            page.wait_for_timeout(3_000)

    def _save_qr_screenshot(self) -> None:
        if self.config.qr_screenshot_path is None or self._count("canvas") == 0:
            return
        try:
            self.config.qr_screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            self._page.screenshot(path=str(self.config.qr_screenshot_path), full_page=True)
            LOGGER.info("QR login screenshot saved to %s", self.config.qr_screenshot_path)
        except PlaywrightError:
            LOGGER.debug("Could not save QR login screenshot.", exc_info=True)

    def _open_allowed_chat(self) -> None:
        page = self._page
        search_term = self.config.allowed_search_term
        search_box = self._first_available(
            [
                "div[contenteditable='true'][data-tab='3']",
                "div[contenteditable='true'][role='textbox']",
                "div[aria-label='Search input textbox']",
            ],
            timeout_ms=30_000,
        )
        search_box.click()
        search_box.fill(search_term)
        page.wait_for_timeout(1_500)

        self._click_allowed_search_result(timeout_ms=20_000)
        page.wait_for_timeout(1_000)
        self._assert_allowed_chat()

    def _click_allowed_search_result(self, timeout_ms: int) -> None:
        deadline = time.time() + timeout_ms / 1000
        selectors = [
            f"span[title={json.dumps(self.config.allowed_chat_name)}]" if self.config.allowed_chat_name else "",
            "div[role='row']",
            "div[role='gridcell']",
        ]
        while time.time() < deadline:
            for selector in selectors:
                if not selector:
                    continue
                rows = self._page.locator(selector)
                for index in range(min(rows.count(), 20)):
                    candidate = rows.nth(index)
                    if self._search_candidate_is_allowed(candidate):
                        candidate.click()
                        LOGGER.info("Allowed chat opened: %s", self.config.allowed_search_term)
                        return
            self._page.wait_for_timeout(250)
        raise RuntimeError(f"Allowed chat was not found in WhatsApp search: {self.config.allowed_search_term!r}")

    def _search_candidate_is_allowed(self, candidate: Locator) -> bool:
        try:
            text = candidate.inner_text(timeout=500)
        except PlaywrightError:
            text = ""
        title = candidate.get_attribute("title") or ""
        searchable = f"{title}\n{text}".strip()
        return self.config.allowed_policy.matches_text(searchable)

    def _monitor_loop(self, context: BrowserContext) -> None:
        LOGGER.info("Monitoring only allowed chat: %s", self.config.allowed_search_term)
        while True:
            try:
                self._assert_allowed_chat()
                self._log_ignored_unread_chats()
                self._process_new_messages(context)
                self.state_store.save(self.state)
            except KeyboardInterrupt:
                raise
            except Exception:
                LOGGER.exception("Polling error; keeping bot alive.")
            time.sleep(self.config.poll_interval_seconds)

    def _process_new_messages(self, context: BrowserContext) -> None:
        incoming = self._page.locator("div.message-in")
        count = incoming.count()
        for index in range(max(0, count - 20), count):
            message = incoming.nth(index)
            key = self._message_key(message, index)
            if key in self.state.processed_message_ids:
                continue

            self.state.processed_message_ids.add(key)
            text = self._message_text(message)
            media_path = self._download_media_if_available(context, message)

            if not text and media_path is None:
                continue

            LOGGER.info("Received allowed incoming message %s text=%s media=%s", key, bool(text), bool(media_path))
            if _env_bool("DEBUG", False) and text:
                LOGGER.debug("Allowed incoming message text: %s", text)
            if media_path is not None:
                response, files = self.conversation.handle_file(media_path, text, self.state)
            else:
                response, files = self.conversation.handle_text(text, self.state)

            self._send_text(response)
            for file_path in files:
                try:
                    self._send_file(file_path)
                except Exception as exc:
                    LOGGER.exception("Failed to send generated file to allowed chat: %s", file_path)
                    self._send_text(
                        "Erreur: le document a ete genere, mais l'envoi de la piece jointe a echoue. "
                        f"Fichier local: {file_path}. Detail: {exc}"
                    )

    def _message_key(self, message: Locator, fallback_index: int) -> str:
        for attr in ("data-id", "id"):
            value = message.get_attribute(attr)
            if value:
                return value
        text = self._message_text(message)
        stable_hash = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]
        return f"{fallback_index}:{stable_hash}"

    @staticmethod
    def _message_text(message: Locator) -> str:
        try:
            text = message.inner_text(timeout=1_000)
        except PlaywrightError:
            return ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        cleaned = [line for line in lines if not re.fullmatch(r"\d{1,2}:\d{2}", line)]
        return "\n".join(cleaned).strip()

    def _download_media_if_available(self, context: BrowserContext, message: Locator) -> Path | None:
        media_hint = self._classify_media(message)
        if media_hint == "text":
            return None

        self.download_dir.mkdir(parents=True, exist_ok=True)
        download_buttons = message.locator(
            "span[data-icon='download'], button[aria-label*='Download'], div[aria-label*='Download']"
        )
        if download_buttons.count() == 0:
            return None

        try:
            with self._page.expect_download(timeout=15_000) as download_info:
                download_buttons.first.click()
            download = download_info.value
            filename = download.suggested_filename or f"whatsapp_{int(time.time())}"
            destination = self.download_dir / filename
            download.save_as(str(destination))
            LOGGER.info("Downloaded %s message media to %s", media_hint, destination)
            return destination
        except PlaywrightTimeoutError:
            LOGGER.warning("Media was detected but no browser download started.")
            return None

    @staticmethod
    def _classify_media(message: Locator) -> str:
        try:
            text = message.inner_text(timeout=500).lower()
        except PlaywrightError:
            text = ""
        if message.locator("img").count() > 0:
            return "image"
        if ".pdf" in text:
            return "pdf"
        if re.search(r"\.(docx?|xlsx?|pptx?|txt|csv)\b", text):
            return "document"
        if message.locator("span[data-icon='document'], [data-testid*='document']").count() > 0:
            return "document"
        return "text"

    def _send_text(self, text: str) -> None:
        if not text.strip():
            return
        self._assert_allowed_chat()
        box = self._first_available(
            [
                "footer div[contenteditable='true'][role='textbox']",
                "footer div[contenteditable='true']",
                "div[aria-label='Type a message']",
            ],
            timeout_ms=10_000,
        )
        box.click()
        box.fill(text[:3500])
        box.press("Enter")
        LOGGER.info("Sent text response to allowed chat.")

    def _send_file(self, path: Path) -> None:
        if not path.exists():
            LOGGER.warning("Generated file does not exist: %s", path)
            return
        self._assert_allowed_chat()
        page = self._page

        attach = self._first_available(
            [
                "button[title='Attach']",
                "div[title='Attach']",
                "span[data-icon='plus']",
                "span[data-icon='attach-menu-plus']",
                "span[data-icon='clip']",
            ],
            timeout_ms=10_000,
        )
        attach.click()
        page.wait_for_timeout(500)

        file_input = self._first_available(["input[type='file']"], timeout_ms=10_000)
        file_input.set_input_files(str(path))
        send = self._first_available(
            [
                "span[data-icon='send']",
                "div[role='button'][aria-label='Send']",
                "button[aria-label='Send']",
            ],
            timeout_ms=30_000,
        )
        self._assert_allowed_chat()
        send.click()
        LOGGER.info("Sent generated file to allowed chat: %s", path)

    def _assert_allowed_chat(self) -> None:
        header_text = self._current_chat_header_text()
        if not self.config.allowed_policy.matches_header(header_text):
            raise RuntimeError(f"Safety stop: current chat is not allowed. Header: {header_text!r}")

    def _current_chat_header_text(self) -> str:
        header = self._first_available(["header", "div[data-testid='conversation-header']"], timeout_ms=10_000)
        return header.inner_text(timeout=2_000).strip()

    def _log_ignored_unread_chats(self) -> None:
        rows = self._page.locator("[aria-label*='unread'], span[aria-label*='unread']")
        count = min(rows.count(), 10)
        for index in range(count):
            try:
                row_text = rows.nth(index).locator("xpath=ancestor::div[@role='row'][1]").inner_text(timeout=500)
            except PlaywrightError:
                continue
            if self.config.allowed_chat_name and self.config.allowed_chat_name in row_text:
                continue
            LOGGER.info("Ignored non-allowed unread chat: %s", row_text.splitlines()[0] if row_text else "unknown")

    def _first_available(self, selectors: list[str], timeout_ms: int) -> Locator:
        deadline = time.time() + timeout_ms / 1000
        last_error: Exception | None = None
        while time.time() < deadline:
            for selector in selectors:
                if not selector:
                    continue
                try:
                    locator = self._page.locator(selector).first
                    if locator.count() > 0:
                        return locator
                except Exception as exc:
                    last_error = exc
            self._page.wait_for_timeout(250)
        raise RuntimeError(f"No matching selector found: {selectors}") from last_error

    def _count(self, selector: str) -> int:
        try:
            return self._page.locator(selector).count()
        except PlaywrightError:
            return 0

    @property
    def _page(self) -> Page:
        if self.page is None:
            raise RuntimeError("Browser page is not initialized.")
        return self.page


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    config = BotSettings.load()
    LOGGER.info(
        "Starting WhatsApp Web bot enabled=%s allowed_chat_name=%s allowed_phone_set=%s api_base_url=%s",
        config.enabled,
        bool(config.allowed_chat_name),
        bool(config.allowed_phone),
        config.api_base_url,
    )
    bot = WhatsAppPlaywrightBot(config)
    bot.run()


if __name__ == "__main__":
    main()
