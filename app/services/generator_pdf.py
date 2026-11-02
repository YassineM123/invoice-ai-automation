from __future__ import annotations

import logging
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from app.config import settings
from app.models import ExtractedDocument, CompanyProfile

LOGGER = logging.getLogger("generator_pdf")


def _generate_pdf_playwright(html: str, output_path: Path) -> Path:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html, wait_until="load")
        page.pdf(
            path=str(output_path),
            format="A4",
            print_background=True,
            margin={"top": "15mm", "bottom": "15mm", "left": "15mm", "right": "15mm"},
        )
        browser.close()
    return output_path


def generate_pdf(doc: ExtractedDocument, company: CompanyProfile, filename: str) -> Path:
    templates_dir = Path(__file__).resolve().parent.parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("document.html")
    html = template.render(doc=doc, company=company)
    output_path = settings.output_path / filename

    try:
        from weasyprint import HTML

        HTML(string=html, base_url=str(templates_dir)).write_pdf(str(output_path))
        return output_path
    except (ImportError, OSError) as exc:
        LOGGER.info("WeasyPrint unavailable (%s), falling back to Playwright Chromium PDF.", exc)
        return _generate_pdf_playwright(html, output_path)
