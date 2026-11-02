from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from app.config import settings
from app.models import CompanyProfile


def load_company_profile() -> CompanyProfile:
    path = Path(settings.company_profile_path)
    if not path.exists():
        return CompanyProfile()
    data = json.loads(path.read_text(encoding="utf-8"))
    return CompanyProfile.model_validate(data)


def save_json(data: dict[str, Any], filename: str) -> Path:
    path = settings.output_path / filename
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def public_url(path: Path) -> str:
    return f"{settings.app_base_url}/outputs/{path.relative_to(settings.output_path).as_posix()}"
