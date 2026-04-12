from __future__ import annotations

from researcher_ai_portal_app import views


def test_build_dataset_placeholder_entry_marks_placeholder_for_manual_fix():
    row = views._build_dataset_placeholder_entry({"title": "My Paper"}, accessions_checked=0)
    assert row["accession"] == "NO_DATASET_REPORTED"
    assert row["source"] == "other"
    assert row["raw_metadata"]["placeholder"] is True
    assert row["raw_metadata"]["placeholder_reason"] == "no_dataset_accessions_resolved"


def test_dataset_rows_exposes_primary_url_and_placeholder_flag():
    rows = views._dataset_rows(
        [
            {
                "accession": "NO_DATASET_REPORTED",
                "source": "other",
                "processed_data_urls": ["https://example.org/ds"],
                "raw_metadata": {"placeholder": True},
            }
        ]
    )
    assert len(rows) == 1
    assert rows[0]["placeholder"] is True
    assert rows[0]["primary_url"] == "https://example.org/ds"


def test_inject_dataset_correction_updates_selected_row_and_clears_placeholder_flag():
    payload = [
        {
            "accession": "NO_DATASET_REPORTED",
            "source": "other",
            "title": "Placeholder",
            "raw_metadata": {"placeholder": True},
            "processed_data_urls": [],
        }
    ]
    updated = views._inject_dataset_correction(
        payload,
        {
            "dataset_index": 0,
            "accession": "GSE314176",
            "source": "geo",
            "title": "Bulk RNA-seq data",
            "organism": "Homo sapiens",
            "experiment_type": "RNA-seq",
            "summary": "Patient and control RNA-seq profiles.",
            "primary_url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE314176",
        },
    )
    assert updated[0]["accession"] == "GSE314176"
    assert updated[0]["source"] == "geo"
    assert updated[0]["source_type"] == "geo"
    assert updated[0]["raw_metadata"]["placeholder"] is False
    assert updated[0]["raw_metadata"]["corrected_by_user"] is True
    assert updated[0]["processed_data_urls"][0].startswith("https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi")
