from __future__ import annotations

from pathlib import Path

from researcher_ai_portal_app.dag_app import _extract_urls


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase5_extract_urls_finds_github_links():
    text = "Code is available at https://github.com/nf-core/rnaseq and docs at https://nf-co.re/rnaseq"
    urls = _extract_urls(text)
    assert "https://github.com/nf-core/rnaseq" in urls
    assert "https://nf-co.re/rnaseq" in urls


def test_phase5_dag_node_detail_mentions_nfcore_and_github():
    dag_path = Path(__file__).resolve().parents[1] / "dag_app.py"
    text = _read(dag_path)
    assert '"nf_core":' in text
    assert '"github_links":' in text
    assert "nf-core:" in text
