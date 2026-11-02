import pytest

from app.config import settings


class _NoOpenAI:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("OpenAI is disabled in tests: they must not consume tokens.")


@pytest.fixture(autouse=True)
def no_openai_tokens(monkeypatch, tmp_path):
    """Every test runs offline: no API key, and any OpenAI client creation fails loudly."""
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr("app.services.extractor.OpenAI", _NoOpenAI)
    monkeypatch.setattr("app.services.item_polisher.OpenAI", _NoOpenAI)
    # Document numbers are taken from a throwaway counter, not the real storage/outputs/counters.json.
    monkeypatch.setattr("app.services.numbering.COUNTER_FILE", tmp_path / "counters.json")
