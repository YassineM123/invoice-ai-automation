from __future__ import annotations

import mimetypes
import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests


LOGGER = logging.getLogger("document_api_client")


class DocumentApiClient:
    def __init__(self, base_url: str, download_dir: Path):
        self.base_url = base_url.rstrip("/")
        self.download_dir = download_dir
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()

    def create_from_text(self, text: str) -> dict:
        response = self.session.post(
            f"{self.base_url}/documents/create-from-text",
            data={"text": text},
            timeout=120,
        )
        response.raise_for_status()
        result = response.json()
        LOGGER.info(
            "Backend create-from-text result: status=%s missing_fields=%s pdf=%s docx=%s report=%s",
            result.get("status"),
            len(result.get("missing_fields") or []),
            bool(result.get("pdf_url")),
            bool(result.get("docx_url")),
            bool(result.get("report_url")),
        )
        return result

    def correct_file(self, path: Path) -> dict:
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        with path.open("rb") as handle:
            response = self.session.post(
                f"{self.base_url}/documents/correct",
                files={"file": (path.name, handle, mime)},
                timeout=180,
            )
        response.raise_for_status()
        result = response.json()
        LOGGER.info(
            "Backend correct-file result: status=%s missing_fields=%s pdf=%s docx=%s report=%s",
            result.get("status"),
            len(result.get("missing_fields") or []),
            bool(result.get("pdf_url")),
            bool(result.get("docx_url")),
            bool(result.get("report_url")),
        )
        return result

    def download_generated_file(self, url_or_path: str | None) -> Path | None:
        if not url_or_path:
            return None
        parsed = urlparse(url_or_path)
        url = self._download_url(url_or_path)
        filename = Path(parsed.path or url).name or "generated_document"
        destination = self.download_dir / filename
        response = self.session.get(url, timeout=120)
        response.raise_for_status()
        destination.write_bytes(response.content)
        LOGGER.info("Downloaded generated backend file to %s", destination)
        return destination

    def health(self) -> bool:
        try:
            response = self.session.get(f"{self.base_url}/health", timeout=10)
            return response.ok
        except requests.RequestException:
            return False

    def _download_url(self, url_or_path: str) -> str:
        parsed = urlparse(url_or_path)
        if not parsed.scheme:
            return urljoin(f"{self.base_url}/", url_or_path.lstrip("/"))

        if parsed.hostname in {"localhost", "127.0.0.1", "0.0.0.0"}:
            internal_base = urlparse(self.base_url)
            return urlunparse(
                (
                    internal_base.scheme,
                    internal_base.netloc,
                    parsed.path,
                    parsed.params,
                    parsed.query,
                    parsed.fragment,
                )
            )

        return url_or_path
