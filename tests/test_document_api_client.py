from pathlib import Path

from app.services.document_api_client import DocumentApiClient


def test_download_url_rewrites_localhost_to_internal_base(tmp_path: Path):
    client = DocumentApiClient("http://api:8000", tmp_path)

    assert (
        client._download_url("http://localhost:8000/outputs/document.pdf")
        == "http://api:8000/outputs/document.pdf"
    )


def test_download_url_keeps_relative_paths_on_internal_base(tmp_path: Path):
    client = DocumentApiClient("http://api:8000", tmp_path)

    assert client._download_url("/outputs/document.pdf") == "http://api:8000/outputs/document.pdf"
