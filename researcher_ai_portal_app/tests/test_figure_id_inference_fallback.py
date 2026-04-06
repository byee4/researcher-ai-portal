from __future__ import annotations

from researcher_ai_portal_app import views


def test_infer_figure_ids_from_paper_text_fallback_extracts_ids():
    class _Sec:
        def __init__(self, text: str):
            self.text = text

    class _Paper:
        raw_text = "Fig. 1 shows quality control. Figure 2 validates expression."
        sections = [
            _Sec("Supplementary Figure S3 contains controls."),
            _Sec("Extended Data Figure 4 has replicates."),
        ]

    inferred = views._infer_figure_ids_from_paper_text(_Paper())
    assert "Figure 1" in inferred
    assert "Figure 2" in inferred
    assert "Supplementary Figure 3" in inferred
    assert "Extended Data Figure 4" in inferred
