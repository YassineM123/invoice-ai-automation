from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from app.config import settings

COUNTER_FILE = settings.counter_file
# Older versions kept the counters inside the public outputs folder.
LEGACY_COUNTER_FILE = settings.output_path / "counters.json"


def _read_counters() -> dict[str, int]:
    source = COUNTER_FILE if COUNTER_FILE.exists() else LEGACY_COUNTER_FILE
    if not source.exists():
        return {}
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except Exception:
        return {}


def next_document_number(document_type: str) -> str:
    prefix = "FAC" if document_type == "facture" else "DEV"
    year = datetime.now().year
    key = f"{prefix}-{year}"
    counters = _read_counters()
    counters[key] = counters.get(key, 0) + 1
    COUNTER_FILE.write_text(json.dumps(counters, indent=2), encoding="utf-8")
    if LEGACY_COUNTER_FILE.exists() and LEGACY_COUNTER_FILE != COUNTER_FILE:
        LEGACY_COUNTER_FILE.unlink()
    return f"{prefix}-{year}-{counters[key]:04d}"
