from __future__ import annotations

import sys
from pathlib import Path


PORTAL_ROOT = Path(__file__).resolve().parents[2]
if str(PORTAL_ROOT) not in sys.path:
    sys.path.insert(0, str(PORTAL_ROOT))

from researcher_ai_portal_app import views


def test_method_assay_rows_returns_plain_english_step_rows():
    rows = views._method_assay_rows(
        {
            "assay_graph": {
                "assays": [
                    {
                        "name": "RNA-seq alignment",
                        "steps": [
                            {
                                "step_number": 1,
                                "description": "Align FASTQ reads to hg38",
                                "software": "STAR",
                                "software_version": "2.7.10a",
                                "input_data": "FASTQ",
                                "output_data": "BAM",
                                "parameters": {"twopassMode": "Basic"},
                                "code_reference": "https://github.com/example/workflow",
                            }
                        ],
                    }
                ]
            }
        }
    )

    assert len(rows) == 1
    assert rows[0]["assay_name"] == "RNA-seq alignment"
    assert len(rows[0]["steps"]) == 1
    first = rows[0]["steps"][0]
    assert first["step_number"] == 1
    assert first["software"] == "STAR"
    assert first["input_data"] == "FASTQ"
    assert first["warnings"] == []


def test_inject_method_step_correction_updates_selected_step_only():
    payload = {
        "assay_graph": {
            "assays": [
                {
                    "name": "RNA-seq alignment",
                    "steps": [
                        {
                            "step_number": 1,
                            "description": "Old description",
                            "software": "OldTool",
                            "software_version": "0.1",
                            "input_data": "old_in",
                            "output_data": "old_out",
                            "parameters": {"old_params": "1"},
                            "code_reference": "old_ref",
                        },
                        {
                            "step_number": 2,
                            "description": "Keep me",
                            "software": "KeepTool",
                        },
                    ],
                }
            ]
        }
    }

    updated = views._inject_method_step_correction(
        payload,
        {
            "assay_index": 0,
            "step_index": 0,
            "description": "Trim adapters and align to hg38",
            "software": "STAR",
            "software_version": "2.7.10a",
            "input_data": "FASTQ.gz",
            "output_data": "sorted BAM",
            "parameters": {"twopassMode": "Basic"},
            "code_reference": "nf-core/rnaseq",
            "inferred_stage_name": "",
        },
    )

    corrected = updated["assay_graph"]["assays"][0]["steps"][0]
    untouched = updated["assay_graph"]["assays"][0]["steps"][1]

    assert corrected["software"] == "STAR"
    assert corrected["output_data"] == "sorted BAM"
    assert untouched["description"] == "Keep me"


def test_inject_method_step_correction_rejects_missing_step_indices():
    payload = {"assay_graph": {"assays": [{"name": "A", "steps": [{"step_number": 1}]}]}}
    try:
        views._inject_method_step_correction(
            payload,
            {
                "assay_index": 0,
                "step_index": 8,
                "description": "bad",
                "software": "",
                "software_version": "",
                "input_data": "",
                "output_data": "",
                "parameters": "",
                "code_reference": "",
                "inferred_stage_name": "",
            },
        )
    except ValueError as exc:
        assert "Selected step was not found" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected ValueError for unknown step index")


def test_method_warning_rows_map_to_assay_and_step_with_severity():
    payload = {
        "assay_graph": {
            "assays": [
                {
                    "name": "RNA-seq alignment",
                    "steps": [
                        {"step_number": 1, "software": "STAR"},
                        {"step_number": 2, "software": "featureCounts"},
                    ],
                }
            ]
        },
        "parse_warnings": [
            "assay_stub: RNA-seq alignment parse failed",
            "inferred_parameters: STAR.outSAMtype=BAM",
        ],
    }
    rows = views._method_warning_rows(payload)
    assert len(rows) == 2
    assert rows[0]["assay_index"] == 0
    assert rows[0]["severity"] == "error"
    assert rows[1]["step_index"] == 0
    assert rows[1]["severity"] == "warning"


def test_template_warning_kv_format_is_classified_and_humanized():
    raw = "assay='iPSC neuron differentiation' template=generic missing=align"
    assert views._warning_category(raw) == "template_missing_stages"
    summary = views._warning_summary(raw)
    assert "iPSC neuron differentiation" in summary
    assert "align" in summary
    stages = views._parse_template_missing_stages(raw)
    assert stages == ["align"]


def test_template_warning_stage_tokens_strip_source_metadata_suffixes():
    raw = (
        "template_missing_stages: assay='iPSC neuron differentiation' template=generic "
        "missing=align,analyze source=partial_skeleton"
    )
    assert views._parse_template_missing_stages(raw) == ["align", "analyze"]
    assert "analyze source=partial_skeleton" not in views._warning_summary(raw)


def test_template_warning_colon_format_strips_source_suffix_variants():
    raw1 = "template_missing_stages: analyze source=partial_skeleton"
    raw2 = "template_missing_stages: analyze source"
    assert views._parse_template_missing_stages(raw1) == ["analyze"]
    assert views._parse_template_missing_stages(raw2) == ["analyze"]


def test_method_assay_rows_add_inferred_stage_skeletons_for_template_warning():
    payload = {
        "assay_graph": {
            "assays": [
                {
                    "name": "RNA-seq alignment",
                    "steps": [{"step_number": 1, "description": "Align", "software": "STAR"}],
                }
            ]
        },
        "parse_warnings": ["template_missing_stages: normalization, differential_expression"],
    }
    rows = views._method_assay_rows(payload)
    assert len(rows) == 1
    assert len(rows[0]["steps"]) == 3
    inferred = [s for s in rows[0]["steps"] if s["is_inferred_stage"]]
    assert len(inferred) == 2
    assert inferred[0]["inferred_stage_name"] == "normalization"


def test_template_stage_items_respect_assay_name_hint_when_index_unresolved():
    warning_rows = [
        {
            "category": "template_missing_stages",
            "raw": "template_missing_stages: align",
            "assay_index": None,
            "assay_name_hint": "iPSC neuron differentiation",
            "warning_index": 0,
        }
    ]
    assay1_items = views._inferred_missing_stage_items_for_assay(
        warning_rows,
        assay_index=0,
        assay_name="RNA-seq alignment",
    )
    assay2_items = views._inferred_missing_stage_items_for_assay(
        warning_rows,
        assay_index=1,
        assay_name="iPSC neuron differentiation",
    )
    assert assay1_items == []
    assert len(assay2_items) == 1
    assert assay2_items[0]["stage_name"] == "align"


def test_inject_method_step_correction_appends_inferred_stage_when_missing():
    payload = {"assay_graph": {"assays": [{"name": "A", "steps": [{"step_number": 1, "description": "x"}]}]}}
    updated = views._inject_method_step_correction(
        payload,
        {
            "assay_index": 0,
            "step_index": 1,
            "description": "Normalize counts",
            "software": "DESeq2",
            "software_version": "1.42.0",
            "input_data": "counts.tsv",
            "output_data": "normalized_counts.tsv",
            "parameters": {"fitType": "parametric"},
            "code_reference": "bioc::DESeq2",
            "inferred_stage_name": "normalization",
            "resolved_warning_indices": "0",
            "inferred_stage_warning_index": 0,
        },
    )
    steps = updated["assay_graph"]["assays"][0]["steps"]
    assert len(steps) == 2
    assert steps[1]["template_stage"] == "normalization"
    assert steps[1]["software"] == "DESeq2"


def test_inject_method_step_correction_appends_inferred_stage_when_virtual_index_is_beyond_len():
    """Regression: second inferred row can carry a virtual index > len(steps)."""
    payload = {"assay_graph": {"assays": [{"name": "A", "steps": [{"step_number": 1, "description": "x"}]}]}}
    updated = views._inject_method_step_correction(
        payload,
        {
            "assay_index": 0,
            "step_index": 2,  # virtual inferred row index (len=1, second suggestion)
            "description": "Analyze expression programs",
            "software": "scanpy",
            "software_version": "1.10.1",
            "input_data": "normalized_counts.tsv",
            "output_data": "umap_embeddings.csv",
            "parameters": {"n_pcs": "30"},
            "code_reference": "scanpy.tl.umap",
            "inferred_stage_name": "analyze",
            "resolved_warning_indices": "0",
            "inferred_stage_warning_index": 0,
        },
    )
    steps = updated["assay_graph"]["assays"][0]["steps"]
    assert len(steps) == 2
    assert steps[1]["template_stage"] == "analyze"
    assert steps[1]["software"] == "scanpy"


def test_remove_method_step_renumbers_remaining_steps():
    payload = {
        "assay_graph": {
            "assays": [
                {
                    "name": "A",
                    "steps": [
                        {"step_number": 1, "description": "A"},
                        {"step_number": 2, "description": "B"},
                        {"step_number": 3, "description": "C"},
                    ],
                }
            ]
        }
    }
    updated = views._remove_method_step(payload, assay_index=0, step_index=1)
    steps = updated["assay_graph"]["assays"][0]["steps"]
    assert [s["description"] for s in steps] == ["A", "C"]
    assert [s["step_number"] for s in steps] == [1, 2]


def test_inject_method_step_correction_removes_resolved_warning_indices():
    payload = {
        "assay_graph": {"assays": [{"name": "A", "steps": [{"step_number": 1, "software": "STAR"}]}]},
        "parse_warnings": [
            "inferred_parameters: STAR.outSAMtype=BAM",
            "paper_rag_vision_fallback: count=1 latency_seconds=0.2",
        ],
    }
    updated = views._inject_method_step_correction(
        payload,
        {
            "assay_index": 0,
            "step_index": 0,
            "description": "desc",
            "software": "STAR",
            "software_version": "2.7.11b",
            "input_data": "FASTQ.gz",
            "output_data": "BAM",
            "parameters": {"outSAMtype": "BAM"},
            "code_reference": "",
            "inferred_stage_name": "",
            "resolved_warning_indices": "0",
            "inferred_stage_warning_index": None,
        },
    )
    assert updated["parse_warnings"] == ["paper_rag_vision_fallback: count=1 latency_seconds=0.2"]
    assert updated["assay_graph"]["assays"][0]["steps"][0]["parameters"] == {"outSAMtype": "BAM"}


def test_clear_template_missing_stage_warning_removes_only_selected_stage():
    payload = {
        "assay_graph": {"assays": []},
        "parse_warnings": [
            "template_missing_stages: normalization, differential_expression",
            "inferred_parameters: foo=bar",
        ],
    }
    updated = views._clear_template_missing_stage_warning(
        payload,
        stage_name="normalization",
        warning_index=0,
    )
    assert updated["parse_warnings"][0] == "template_missing_stages: differential_expression"
    assert len(updated["parse_warnings"]) == 2


def test_clear_template_missing_stages_by_pairs_supports_batch_remove():
    payload = {
        "assay_graph": {"assays": []},
        "parse_warnings": [
            "template_missing_stages: align, quantify",
            "template_missing_stages: normalize",
        ],
    }
    updated = views._clear_template_missing_stages_by_pairs(
        payload,
        ["0::align", "1::normalize"],
    )
    assert updated["parse_warnings"] == ["template_missing_stages: quantify"]


def test_remove_method_steps_batch_handles_mixed_persisted_and_inferred_rows():
    payload = {
        "assay_graph": {
            "assays": [
                {
                    "name": "RNA-seq",
                    "steps": [
                        {"step_number": 1, "description": "qc"},
                        {"step_number": 2, "description": "align"},
                        {"step_number": 3, "description": "quantify"},
                    ],
                }
            ]
        },
        "parse_warnings": [
            "inferred_parameters: assay='RNA-seq' updated_steps=1",
            "template_missing_stages: analyze, differential",
        ],
    }
    updated = views._remove_method_steps_batch(
        payload,
        assay_index=0,
        selected_rows=[
            {
                "step_index": 1,
                "is_inferred_stage": False,
                "warning_indices": [0],
            },
            {
                "step_index": 3,
                "is_inferred_stage": True,
                "inferred_stage_name": "analyze",
                "inferred_stage_warning_index": 1,
                "warning_indices": [],
            },
        ],
    )
    steps = updated["assay_graph"]["assays"][0]["steps"]
    assert [s["description"] for s in steps] == ["qc", "quantify"]
    assert updated["parse_warnings"] == ["template_missing_stages: differential"]


def test_parse_step_batch_payload_parses_checkbox_value():
    parsed = views._parse_step_batch_payload("2||1||analyze||4||0,3")
    assert parsed is not None
    assert parsed["step_index"] == 2
    assert parsed["is_inferred_stage"] is True
    assert parsed["inferred_stage_name"] == "analyze"
    assert parsed["inferred_stage_warning_index"] == 4
    assert parsed["warning_indices"] == [0, 3]


def test_method_assay_rows_normalizes_non_dict_parameters_to_empty_dict():
    rows = views._method_assay_rows(
        {
            "assay_graph": {
                "assays": [
                    {
                        "name": "A",
                        "steps": [
                            {"step_number": 1, "software": "STAR", "parameters": "bad-string"}
                        ],
                    }
                ]
            }
        }
    )
    first = rows[0]["steps"][0]
    assert first["parameters"] == {}
    assert first["parameters_json"] == "{}"
