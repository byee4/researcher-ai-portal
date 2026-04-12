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
                                "parameters": "--twopassMode Basic",
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
                            "parameters": "old_params",
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
            "parameters": "--twopassMode Basic",
            "code_reference": "nf-core/rnaseq",
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
            },
        )
    except ValueError as exc:
        assert "Selected step was not found" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected ValueError for unknown step index")
