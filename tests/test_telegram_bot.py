from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.bot.telegram_bot import (
    TelegramBot,
    TelegramBotConfig,
    TelegramStateStore,
)
from app.bot.conversation import ChatState


class FakeApiClient:
    def create_from_text(self, text: str) -> dict:
        return {
            "status": "ready",
            "pdf_url": "http://localhost:8000/outputs/test.pdf",
            "docx_url": "http://localhost:8000/outputs/test.docx",
            "report_url": "http://localhost:8000/outputs/test_report.json",
            "missing_fields": [],
        }

    def correct_file(self, path: Path) -> dict:
        return {
            "status": "ready",
            "pdf_url": "http://localhost:8000/outputs/corrected.pdf",
            "docx_url": None,
            "report_url": None,
            "missing_fields": [],
        }

    def download_generated_file(self, url_or_path: str | None) -> Path | None:
        if not url_or_path:
            return None
        return Path(url_or_path)

    def health(self) -> bool:
        return True


@pytest.fixture
def temp_dirs():
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        download_dir = base / "downloads"
        download_dir.mkdir(parents=True, exist_ok=True)
        session_dir = base / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        generated_dir = base / "generated"
        generated_dir.mkdir(parents=True, exist_ok=True)
        yield download_dir, session_dir, generated_dir


@pytest.fixture
def bot_config(temp_dirs):
    download_dir, session_dir, generated_dir = temp_dirs
    return TelegramBotConfig(
        bot_token="fake_token_123",
        allowed_chat_ids={"12345", "99999"},
        api_base_url="http://localhost:8000",
        poll_interval_seconds=1,
        download_dir=download_dir,
        session_dir=session_dir,
        generated_dir=generated_dir,
    )


def test_telegram_state_store(temp_dirs):
    _, session_dir, _ = temp_dirs
    store = TelegramStateStore(session_dir)

    state = store.load("123")
    assert state.step == "waiting_for_document_type"

    state.step = "collecting_client_info"
    state.data["client_name"] = "Jean Dupont"
    store.save("123", state)

    reloaded = store.load("123")
    assert reloaded.step == "collecting_client_info"
    assert reloaded.data["client_name"] == "Jean Dupont"


def test_allowed_chats(bot_config):
    bot = TelegramBot(bot_config, api_client=FakeApiClient())
    assert bot.is_chat_allowed("12345") is True
    assert bot.is_chat_allowed("99999") is True
    assert bot.is_chat_allowed("11111") is False

    # When allowed_chat_ids is empty, any chat is allowed
    open_config = TelegramBotConfig(
        bot_token="fake",
        allowed_chat_ids=set(),
        api_base_url="http://localhost:8000",
        poll_interval_seconds=1,
        download_dir=bot_config.download_dir,
        session_dir=bot_config.session_dir,
        generated_dir=bot_config.generated_dir,
    )
    open_bot = TelegramBot(open_config, api_client=FakeApiClient())
    assert open_bot.is_chat_allowed("11111") is True


def test_process_start_command(bot_config):
    bot = TelegramBot(bot_config, api_client=FakeApiClient())
    bot.send_message = MagicMock(return_value=True)

    message = {
        "message_id": 1,
        "chat": {"id": 12345},
        "from": {"id": 12345},
        "text": "/start",
    }
    bot.process_message(message)

    bot.send_message.assert_called_once()
    args, _ = bot.send_message.call_args
    assert args[0] == 12345
    assert "Bonjour" in args[1]


def test_process_unauthorized_chat(bot_config):
    bot = TelegramBot(bot_config, api_client=FakeApiClient())
    bot.send_message = MagicMock(return_value=True)

    message = {
        "message_id": 2,
        "chat": {"id": 9999999},  # not in whitelist
        "from": {"id": 9999999},
        "text": "Hello",
    }
    bot.process_message(message)

    bot.send_message.assert_called_once()
    args, _ = bot.send_message.call_args
    assert "non autorisé" in args[1]


def test_process_duplicate_message_id_ignored(bot_config):
    bot = TelegramBot(bot_config, api_client=FakeApiClient())
    bot.send_message = MagicMock(return_value=True)

    message = {
        "message_id": 5,
        "chat": {"id": 12345},
        "from": {"id": 12345},
        "text": "/help",
    }
    bot.process_message(message)
    assert bot.send_message.call_count == 1

    # Sending same message_id should be ignored
    bot.process_message(message)
    assert bot.send_message.call_count == 1


def test_process_document_message(bot_config, temp_dirs):
    download_dir, _, _ = temp_dirs
    fake_downloaded = download_dir / "invoice.txt"
    fake_downloaded.write_text("Facture de test", encoding="utf-8")

    bot = TelegramBot(bot_config, api_client=FakeApiClient())
    bot.download_telegram_file = MagicMock(return_value=fake_downloaded)
    bot.send_message = MagicMock(return_value=True)
    bot.send_document = MagicMock(return_value=True)

    message = {
        "message_id": 10,
        "chat": {"id": 12345},
        "from": {"id": 12345},
        "document": {
            "file_id": "file_abc",
            "file_name": "invoice.txt",
        },
        "caption": "test devis",
    }
    bot.process_message(message)

    bot.download_telegram_file.assert_called_once_with("file_abc", "invoice.txt")
    bot.send_message.assert_called_once()
    assert "pret" in bot.send_message.call_args[0][1].lower()
