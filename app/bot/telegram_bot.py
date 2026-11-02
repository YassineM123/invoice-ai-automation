from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from app.bot.conversation import ChatState, ConversationManager
from app.config import settings
from app.services.document_api_client import DocumentApiClient

LOGGER = logging.getLogger("telegram_bot")


class TelegramStateStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def _state_file(self, chat_id: int | str) -> Path:
        return self.directory / f"chat_{chat_id}.json"

    def load(self, chat_id: int | str) -> ChatState:
        path = self._state_file(chat_id)
        if not path.exists():
            return ChatState()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ChatState.from_dict(data)
        except Exception as exc:
            LOGGER.warning("Could not read state file for chat %s: %s", chat_id, exc)
            return ChatState()

    def save(self, chat_id: int | str, state: ChatState) -> None:
        path = self._state_file(chat_id)
        try:
            path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            LOGGER.error("Could not save state for chat %s: %s", chat_id, exc)


@dataclass(frozen=True)
class TelegramBotConfig:
    bot_token: str
    allowed_chat_ids: set[str]
    api_base_url: str
    poll_interval_seconds: int
    download_dir: Path
    session_dir: Path
    generated_dir: Path

    @classmethod
    def load(cls) -> "TelegramBotConfig":
        load_dotenv()
        token = os.getenv("TELEGRAM_BOT_TOKEN", settings.telegram_bot_token).strip()
        raw_allowed = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", settings.telegram_allowed_chat_ids).strip()
        allowed_chat_ids = {item.strip() for item in raw_allowed.split(",") if item.strip()}

        api_base_url = (
            os.getenv("API_BASE_URL", os.getenv("APP_BASE_URL", settings.api_base_url)).strip().rstrip("/")
        )
        poll_interval = int(os.getenv("TELEGRAM_POLL_INTERVAL_SECONDS", str(settings.telegram_poll_interval_seconds)))
        download_dir = Path(os.getenv("TELEGRAM_DOWNLOAD_DIR", settings.telegram_download_dir))
        session_dir = Path(os.getenv("TELEGRAM_SESSION_DIR", settings.telegram_session_dir))
        generated_dir = session_dir.parent / "telegram-generated"

        return cls(
            bot_token=token,
            allowed_chat_ids=allowed_chat_ids,
            api_base_url=api_base_url,
            poll_interval_seconds=max(1, poll_interval),
            download_dir=download_dir,
            session_dir=session_dir,
            generated_dir=generated_dir,
        )

    def validate(self) -> None:
        if not self.bot_token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN is not configured. Set TELEGRAM_BOT_TOKEN in your .env or environment."
            )
        if not self.api_base_url:
            raise RuntimeError("API_BASE_URL is required.")
        if not self.allowed_chat_ids:
            raise RuntimeError(
                "TELEGRAM_ALLOWED_CHAT_IDS is empty: the bot would answer anyone. "
                "Set your chat id(s) in .env (comma separated)."
            )


class TelegramBot:
    def __init__(self, config: TelegramBotConfig, api_client: DocumentApiClient | None = None):
        self.config = config
        self.config.download_dir.mkdir(parents=True, exist_ok=True)
        self.config.session_dir.mkdir(parents=True, exist_ok=True)
        self.config.generated_dir.mkdir(parents=True, exist_ok=True)

        self.api_url = f"https://api.telegram.org/bot{self.config.bot_token}"
        self.file_api_url = f"https://api.telegram.org/file/bot{self.config.bot_token}"

        self.session = requests.Session()
        self.api_client = api_client or DocumentApiClient(
            base_url=self.config.api_base_url,
            download_dir=self.config.generated_dir,
        )
        self.conversation_manager = ConversationManager(self.api_client)
        self.state_store = TelegramStateStore(self.config.session_dir)

    def _redact(self, exc: Exception) -> str:
        """Telegram errors contain the request URL, and the URL contains the bot token."""
        text = str(exc)
        return text.replace(self.config.bot_token, "***") if self.config.bot_token else text

    def is_chat_allowed(self, chat_id: int | str, user_id: int | str | None = None) -> bool:
        # Deny by default: with no allowed id configured, nobody can use the bot.
        if not self.config.allowed_chat_ids:
            return False
        str_chat_id = str(chat_id)
        str_user_id = str(user_id) if user_id is not None else ""
        return str_chat_id in self.config.allowed_chat_ids or str_user_id in self.config.allowed_chat_ids

    def get_me(self) -> dict[str, Any]:
        response = self.session.get(f"{self.api_url}/getMe", timeout=15)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram getMe failed: {data}")
        return data["result"]

    def get_updates(self, offset: int | None = None, timeout: int = 20) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        try:
            response = self.session.get(f"{self.api_url}/getUpdates", params=params, timeout=timeout + 10)
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                LOGGER.error("getUpdates returned non-ok: %s", data)
                return []
            return data.get("result", [])
        except requests.RequestException as exc:
            LOGGER.warning("Telegram polling error: %s", self._redact(exc))
            return []

    def send_message(self, chat_id: int | str, text: str) -> bool:
        try:
            response = self.session.post(
                f"{self.api_url}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=20,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            LOGGER.error("Failed to send message to chat %s: %s", chat_id, self._redact(exc))
            return False

    def send_document(self, chat_id: int | str, document_path: Path, caption: str = "") -> bool:
        if not document_path.exists():
            LOGGER.warning("File does not exist to send: %s", document_path)
            return False
        try:
            with document_path.open("rb") as handle:
                files = {"document": (document_path.name, handle)}
                data = {"chat_id": str(chat_id)}
                if caption:
                    data["caption"] = caption
                response = self.session.post(
                    f"{self.api_url}/sendDocument",
                    data=data,
                    files=files,
                    timeout=60,
                )
                response.raise_for_status()
                LOGGER.info("Sent document %s to chat %s", document_path.name, chat_id)
                return True
        except requests.RequestException as exc:
            LOGGER.error("Failed to send document %s to %s: %s", document_path, chat_id, self._redact(exc))
            return False

    def download_telegram_file(self, file_id: str, suggested_name: str | None = None) -> Path | None:
        try:
            res = self.session.get(f"{self.api_url}/getFile", params={"file_id": file_id}, timeout=20)
            res.raise_for_status()
            result = res.json()
            if not result.get("ok"):
                LOGGER.error("getFile failed: %s", result)
                return None
            remote_path = result["result"]["file_path"]
            download_url = f"{self.file_api_url}/{remote_path}"

            # Never trust the sender's file name ("../../app/main.py"): keep only a safe extension.
            file_ext = Path(suggested_name or remote_path).suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,8}", file_ext):
                file_ext = ".bin"
            local_path = self.config.download_dir / f"telegram_{uuid.uuid4().hex}{file_ext}"

            file_res = self.session.get(download_url, timeout=60)
            file_res.raise_for_status()
            local_path.write_bytes(file_res.content)
            LOGGER.info("Downloaded Telegram file %s to %s", file_id, local_path)
            return local_path
        except requests.RequestException as exc:
            LOGGER.error("Failed downloading telegram file %s: %s", file_id, self._redact(exc))
            return None

    def process_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        user = message.get("from") or {}
        user_id = user.get("id")
        message_id = message.get("message_id")

        if not chat_id:
            return

        if not self.is_chat_allowed(chat_id, user_id):
            LOGGER.warning("Unauthorized access attempt from chat_id=%s, user_id=%s", chat_id, user_id)
            self.send_message(
                chat_id,
                "Accès non autorisé. Veuillez configurer TELEGRAM_ALLOWED_CHAT_IDS pour autoriser cet identifiant.",
            )
            return

        state = self.state_store.load(chat_id)
        str_msg_id = str(message_id)
        if str_msg_id in state.processed_message_ids:
            return
        state.processed_message_ids.add(str_msg_id)

        text = message.get("text")
        caption = message.get("caption") or ""
        document = message.get("document")
        photo = message.get("photo")

        reply_text = ""
        files_to_send: list[Path] = []

        try:
            if document:
                file_id = document.get("file_id")
                file_name = document.get("file_name")
                downloaded = self.download_telegram_file(file_id, file_name)
                if downloaded:
                    reply_text, files_to_send = self.conversation_manager.handle_file(
                        downloaded, caption, state
                    )
                else:
                    reply_text = "Impossible de télécharger le fichier. Veuillez réessayer."
            elif photo:
                largest_photo = photo[-1]
                file_id = largest_photo.get("file_id")
                downloaded = self.download_telegram_file(file_id, f"photo_{message_id}.jpg")
                if downloaded:
                    reply_text, files_to_send = self.conversation_manager.handle_file(
                        downloaded, caption, state
                    )
                else:
                    reply_text = "Impossible de télécharger la photo. Veuillez réessayer."
            elif text:
                reply_text, files_to_send = self.conversation_manager.handle_text(text, state)
            else:
                reply_text = "Veuillez envoyer un texte décrivant la prestation ou un document (PDF, image, texte)."

        except Exception as exc:
            LOGGER.error("Error processing message %s from chat %s: %s", message_id, chat_id, self._redact(exc))
            reply_text = "Une erreur s'est produite lors du traitement. Veuillez reessayer."

        self.state_store.save(chat_id, state)

        if reply_text:
            self.send_message(chat_id, reply_text)

        # Only send PDF and DOCX — never send the JSON correction report
        for file_path in files_to_send:
            if file_path.suffix.lower() != ".json":
                self.send_document(chat_id, file_path)

    def run_polling(self) -> None:
        self.config.validate()
        bot_info = self.get_me()
        LOGGER.info(
            "Telegram bot connected successfully as @%s (%s). Starting polling...",
            bot_info.get("username"),
            bot_info.get("first_name"),
        )
        print(f"Telegram bot running as @{bot_info.get('username')}. Press Ctrl+C to stop.")

        offset: int | None = None
        while True:
            try:
                updates = self.get_updates(offset=offset, timeout=20)
                for update in updates:
                    update_id = update.get("update_id")
                    if update_id is not None:
                        offset = update_id + 1

                    message = update.get("message")
                    if message:
                        self.process_message(message)

                time.sleep(self.config.poll_interval_seconds)
            except KeyboardInterrupt:
                LOGGER.info("Telegram bot stopped by user.")
                print("\nTelegram bot stopped.")
                break
            except Exception as exc:
                LOGGER.error("Unexpected error in Telegram polling loop: %s", self._redact(exc))
                time.sleep(3)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = TelegramBotConfig.load()
    bot = TelegramBot(config)
    bot.run_polling()


if __name__ == "__main__":
    main()

