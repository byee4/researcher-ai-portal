from __future__ import annotations

from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from researcher_ai.models.paper import Paper, PaperSource

from researcher_ai_portal_app import views
from researcher_ai_portal_app.job_store import create_job


def test_stage_uploaded_pdf_persists_absolute_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(views, "PDF_STAGE_DIR", tmp_path)
    uploaded = SimpleUploadedFile("paper.pdf", b"%PDF-1.4\nmock-pdf-content\n", content_type="application/pdf")

    staged_path = views._stage_uploaded_pdf(uploaded)

    assert staged_path.is_absolute()
    assert staged_path.exists()
    assert staged_path.suffix.lower() == ".pdf"
    assert staged_path.read_bytes() == b"%PDF-1.4\nmock-pdf-content\n"


def test_run_step_paper_fails_when_staged_pdf_missing():
    missing_pdf = "/tmp/researcher-ai-portal-missing-paper.pdf"
    job_id = create_job(
        input_type="pdf",
        input_value="paper.pdf",
        source=missing_pdf,
        source_type="pdf",
        llm_model="gpt-5.4",
        llm_api_key="sk-12345678901234567890",
    )

    with pytest.raises(FileNotFoundError, match="Uploaded PDF is no longer available"):
        views._run_step(job_id, "paper")


def test_run_step_figures_fails_when_staged_pdf_missing():
    paper = Paper(
        title="PDF Figure Guard",
        source=PaperSource.PDF,
        source_path="/tmp/researcher-ai-portal-missing-figures.pdf",
        figure_ids=["Figure 1"],
    )
    job_id = create_job(
        input_type="pdf",
        input_value="paper.pdf",
        source="/tmp/researcher-ai-portal-missing-figures.pdf",
        source_type="pdf",
        llm_model="gpt-5.4",
        llm_api_key="sk-12345678901234567890",
        components={"paper": paper.model_dump(mode="json")},
    )

    with pytest.raises(FileNotFoundError, match="Staged PDF for figure parsing was not found"):
        views._run_step(job_id, "figures")
