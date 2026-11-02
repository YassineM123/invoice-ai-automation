from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "French Invoice AI Automation"
    app_base_url: str = "http://localhost:8000"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    default_vat_rate: float = 20.0
    default_payment_days: int = 30
    verify_token: str = "change-me"
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_enabled: bool = False
    whatsapp_web_enabled: bool = False
    whatsapp_allowed_chat_name: str = ""
    whatsapp_allowed_phone: str = ""
    whatsapp_session_dir: str = "./runtime/whatsapp-session"
    whatsapp_download_dir: str = "./runtime/whatsapp-downloads"
    whatsapp_allow_group: bool = False
    whatsapp_allow_groups: bool = False
    whatsapp_poll_interval_seconds: int = 3
    whatsapp_headless: bool = False
    whatsapp_qr_screenshot_path: str = "./runtime/whatsapp-qr-login.png"
    telegram_bot_token: str = ""
    telegram_allowed_chat_ids: str = ""
    telegram_download_dir: str = "./runtime/telegram-downloads"
    telegram_session_dir: str = "./runtime/telegram-sessions"
    telegram_poll_interval_seconds: int = 2
    api_base_url: str = "http://localhost:8000"
    company_profile_path: str = "app/company_profile.json"
    upload_dir: str = "storage/uploads"
    output_dir: str = "storage/outputs"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def upload_path(self) -> Path:
        return Path(self.upload_dir)

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir)


settings = Settings()
settings.upload_path.mkdir(parents=True, exist_ok=True)
settings.output_path.mkdir(parents=True, exist_ok=True)
