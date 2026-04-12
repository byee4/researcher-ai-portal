from __future__ import annotations

from researcher_ai_portal_app.confidence import compute_confidence
from researcher_ai_portal_app.views import _dashboard_context


def test_phase2_compute_confidence_scores_assays_and_pipeline():
    components = {
        "paper": {"title": "Test Paper"},
        "figures": [
            {
                "figure_id": "Figure 1",
                "caption": "RNA-seq assay quality control and alignment",
                "subfigures": [{"composite_confidence": 90.0}, {"classification_confidence": 0.8}],
            }
        ],
        "method": {
            "assay_graph": {
                "assays": [
                    {
                        "name": "RNA-seq",
                        "raw_data_source": "GEO: GSE12345",
                        "steps": [
                            {
                                "software": "STAR",
                                "software_version": "2.7.11a",
                                "parameters": {"outFilterMismatchNmax": 2},
                                "input_data": "FASTQ",
                                "output_data": "BAM",
                            }
                        ],
                    }
                ]
            },
            "parse_warnings": [],
        },
        "datasets": [{"accession": "GSE12345"}],
        "software": [{"name": "STAR"}],
        "pipeline": {"validation_report": {"passed": True}},
    }

    conf = compute_confidence(components)
    assert conf["validation_passed"] is True
    assert "RNA-seq" in conf["assay_confidences"]
    assert conf["assay_confidences"]["RNA-seq"]["dataset_resolved"] is True
    assert conf["overall"] >= 80.0


def test_phase2_dashboard_context_exposes_confidence_summary():
    job = {
        "components": {
            "paper": {"title": "Demo"},
            "figures": [],
            "method": {"assay_graph": {"assays": []}},
            "datasets": [],
            "software": [],
            "pipeline": {},
        },
        "component_meta": {},
    }
    ctx = _dashboard_context(job)
    assert "confidence" in ctx
    assert "overall_confidence" in ctx["summary"]


def test_phase2_dataset_resolved_when_experiment_type_matches_assay_name():
    components = {
        "paper": {"title": "Test Paper"},
        "figures": [],
        "method": {
            "assay_graph": {
                "assays": [
                    {
                        "name": "RNA-seq",
                        "raw_data_source": "",
                        "steps": [],
                    }
                ]
            },
            "parse_warnings": [],
        },
        "datasets": [{"accession": "NO_DATASET_REPORTED", "experiment_type": "RNA seq profiling"}],
        "software": [],
        "pipeline": {"validation_report": {"passed": True}},
    }

    conf = compute_confidence(components)
    assert conf["assay_confidences"]["RNA-seq"]["dataset_resolved"] is True


def test_phase2_dataset_unresolved_when_experiment_type_is_unrelated():
    components = {
        "paper": {"title": "Test Paper"},
        "figures": [],
        "method": {
            "assay_graph": {
                "assays": [
                    {
                        "name": "RNA-seq",
                        "raw_data_source": "",
                        "steps": [],
                    }
                ]
            },
            "parse_warnings": [],
        },
        "datasets": [{"accession": "NO_DATASET_REPORTED", "experiment_type": "proteomics"}],
        "software": [],
        "pipeline": {"validation_report": {"passed": True}},
    }

    conf = compute_confidence(components)
    assert conf["assay_confidences"]["RNA-seq"]["dataset_resolved"] is False
