from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


PORTAL_ROOT = Path(__file__).resolve().parents[2]
if str(PORTAL_ROOT) not in sys.path:
    sys.path.insert(0, str(PORTAL_ROOT))

from researcher_ai_portal_app import views


def test_inject_figure_ground_truth_creates_new_figure_and_panel():
    payload = []
    updated = views._inject_figure_ground_truth(
        payload,
        {
            "figure_id": "Figure 9",
            "panel_label": "A",
            "plot_type": "bar",
            "plot_category": "categorical",
            "title_override": "Ground Truth Figure 9",
            "caption_override": "Figure 9 caption",
            "x_axis_label": "Condition",
            "x_axis_scale": "categorical",
            "y_axis_label": "Signal",
            "y_axis_scale": "linear",
            "description": "Manual correction",
            "mark_uncertain": False,
        },
    )
    assert len(updated) == 1
    fig = updated[0]
    assert fig["figure_id"] == "Figure 9"
    assert fig["title"] == "Ground Truth Figure 9"
    assert fig["caption"] == "Figure 9 caption"
    assert len(fig["subfigures"]) == 1
    sf = fig["subfigures"][0]
    assert sf["plot_type"] == "bar"
    assert sf["layers"][0]["plot_type"] == "bar"
    assert sf["x_axis"]["label"] == "Condition"
    assert sf["x_axis"]["scale"] == "categorical"
    assert sf["y_axis"]["label"] == "Signal"


def test_inject_figure_ground_truth_updates_existing_panel_and_marks_uncertain():
    payload = [
        {
            "figure_id": "Figure 1",
            "title": "Old title",
            "caption": "Old caption",
            "purpose": "Old purpose",
            "subfigures": [
                {
                    "label": "A",
                    "description": "Old panel",
                    "plot_type": "other",
                    "plot_category": "composite",
                    "layers": [{"plot_type": "other", "is_primary": True}],
                    "classification_confidence": 0.8,
                    "evidence_spans": [],
                }
            ],
        }
    ]

    updated = views._inject_figure_ground_truth(
        payload,
        {
            "figure_id": "Figure 1",
            "panel_label": "A",
            "plot_type": "venn",
            "plot_category": "flow",
            "title_override": "",
            "caption_override": "",
            "x_axis_label": "",
            "x_axis_scale": "",
            "y_axis_label": "",
            "y_axis_scale": "",
            "description": "",
            "mark_uncertain": True,
        },
    )

    sf = updated[0]["subfigures"][0]
    assert sf["plot_type"] == "venn"
    assert sf["plot_category"] == "flow"
    assert sf["classification_confidence"] == 0.2
    assert "ground_truth_marked_uncertain" in sf["evidence_spans"]


def test_figure_uncertainty_rows_flags_missing_and_unknown_fields():
    rows = views._figure_uncertainty_rows(
        [
            {
                "figure_id": "Figure 2",
                "title": "",
                "caption": "",
                "purpose": "Could not be parsed.",
                "subfigures": [
                    {
                        "label": "A",
                        "plot_type": "other",
                        "classification_confidence": 0.4,
                    }
                ],
            }
        ]
    )
    assert len(rows) == 1
    reasons = rows[0]["reasons"]
    assert "missing_title" in reasons
    assert "missing_caption" in reasons
    assert "figure_unparsed" in reasons
    assert "panel_A:unknown_plot_type" in reasons
    assert "panel_A:low_confidence" in reasons


def test_figure_provenance_rows_collects_calibration_and_ground_truth_tags():
    rows = views._figure_provenance_rows(
        [
            {
                "figure_id": "Figure 1",
                "subfigures": [
                    {
                        "label": "A",
                        "plot_type": "bar",
                        "classification_confidence": 0.99,
                        "evidence_spans": [
                            "calibration_rule:pmc11633308_fig1_abc",
                            "ground_truth_injected",
                            "other_evidence",
                        ],
                    },
                    {
                        "label": "B",
                        "plot_type": "other",
                        "classification_confidence": 0.3,
                        "evidence_spans": ["other_evidence_only"],
                    },
                ],
            }
        ]
    )
    assert len(rows) == 1
    assert rows[0]["figure_id"] == "Figure 1"
    assert len(rows[0]["panels"]) == 1
    panel = rows[0]["panels"][0]
    assert panel["label"] == "A"
    assert panel["plot_type"] == "bar"
    assert "calibration_rule:pmc11633308_fig1_abc" in panel["calibration_rules"]
    assert "ground_truth_injected" in panel["ground_truth_tags"]


def test_extract_figure_image_urls_collects_from_keys_and_caption_text():
    fig = {
        "figure_id": "Figure 1",
        "caption": "See source at https://example.org/fig1.png.",
        "subfigures": [
            {
                "label": "A",
                "image_url": "https://cdn.example.org/panelA.jpg",
                "description": "panel",
            }
        ],
    }
    urls = views._extract_figure_image_urls(fig)
    assert "https://example.org/fig1.png" in urls
    assert "https://cdn.example.org/panelA.jpg" in urls


def test_looks_like_image_url_accepts_common_image_patterns():
    assert views._looks_like_image_url("https://example.org/image.png")
    assert views._looks_like_image_url("https://pmc.ncbi.nlm.nih.gov/articles/PMC123/figure/F1/")
    assert not views._looks_like_image_url("https://example.org/article")


def test_build_pmc_figure_url_uses_f_prefix_not_literal_figure_label():
    url = views._build_pmc_figure_url("PMC12283108", "Figure 1")
    assert url == "https://pmc.ncbi.nlm.nih.gov/articles/PMC12283108/figure/F1/"


def test_split_primary_and_supplementary_figure_ids():
    primary, supp = views._split_primary_and_supplementary_figure_ids(
        ["Figure 1", "Fig. 2A", "Supplementary Figure 1", "Extended Data Figure 3"]
    )
    assert primary == ["Figure 1", "Fig. 2A"]
    assert supp == ["Supplementary Figure 1", "Extended Data Figure 3"]


def test_figure_media_rows_marks_supplementary_as_deferred_parser():
    rows = views._figure_media_rows(
        [{"figure_id": "Supplementary Figure 5", "title": "Supp 5", "caption": "", "purpose": ""}],
        {"pmcid": "PMC12283108"},
        "job123",
    )
    assert len(rows) == 1
    assert rows[0]["entries"] == []
    assert rows[0]["deferred_parser"] == "Supplemental Figure Parser"


@patch("httpx.Client")
def test_pick_first_valid_url_accepts_html_figure_endpoint(mock_client_cls):
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "text/html; charset=utf-8"}
    resp.url = "https://pmc.ncbi.nlm.nih.gov/articles/PMC12283108/figure/F1/"
    client.get.return_value = resp
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = False
    mock_client_cls.return_value = cm

    picked = views._pick_first_valid_url(["https://pmc.ncbi.nlm.nih.gov/articles/PMC12283108/figure/F1/"])
    assert picked == "https://pmc.ncbi.nlm.nih.gov/articles/PMC12283108/figure/F1/"


@patch("httpx.Client")
def test_pick_first_valid_url_rejects_blocked_placeholder(mock_client_cls):
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "image/svg+xml"}
    resp.url = "https://cdn.ncbi.nlm.nih.gov/pmc/pd-medc-pmc-cloudpmc-viewer/production/a2b04810/var/data/static/img/us_flag.svg"
    client.get.return_value = resp
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = False
    mock_client_cls.return_value = cm

    picked = views._pick_first_valid_url(["https://pmc.ncbi.nlm.nih.gov/articles/PMC12283108/figure/F1/"])
    assert picked is None
