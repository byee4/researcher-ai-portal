from __future__ import annotations

import textwrap

from researcher_ai.models.paper import Paper, PaperSource, Section
from researcher_ai.utils.pubmed import parse_jats_xml

from researcher_ai_portal_app.job_store import create_job, get_job
from researcher_ai_portal_app import views


JATS_KEY_RESOURCES_FIXTURE = textwrap.dedent(
    """\
    <article>
      <front>
        <article-meta>
          <article-id pub-id-type="pmid">11633308</article-id>
          <title-group><article-title>Portal key resources dataset-step test</article-title></title-group>
        </article-meta>
      </front>
      <body>
        <sec>
          <title>Methods</title>
          <table-wrap id="t1">
            <label>Table 1</label>
            <caption><title>Key Resources Table</title></caption>
            <table>
              <tbody>
                <tr><td>Dataset</td><td>GSE314176</td></tr>
                <tr><td>SRA project</td><td>SRP123456</td></tr>
              </tbody>
            </table>
          </table-wrap>
        </sec>
      </body>
    </article>
"""
)


class _StubDataset:
    def __init__(self, accession: str):
        self.accession = accession

    def model_dump(self, mode: str = "json"):
        return {"accession": self.accession}


class _StubGEOParser:
    def parse(self, accession: str):
        return _StubDataset(accession)


class _StubSRAParser:
    def parse(self, accession: str):
        return _StubDataset(accession)


def test_portal_dataset_step_collects_key_resources_table_accessions(monkeypatch):
    """Datasets step should collect IDs listed only in Key Resources Table text."""
    parsed = parse_jats_xml(JATS_KEY_RESOURCES_FIXTURE)
    paper = Paper(
        title=parsed.get("title", ""),
        pmid=parsed.get("pmid"),
        source=PaperSource.PMCID,
        source_path="PMC11633308",
        sections=[Section(title=s["title"], text=s["text"]) for s in parsed.get("sections", [])],
        raw_text="",  # ensure dataset IDs come from parsed section/table text, not raw fallback
    )

    job_id = create_job(
        input_type="pmid",
        input_value="11633308",
        source="11633308",
        source_type="pmid",
        llm_model="gpt-5.4",
        llm_api_key="sk-test-placeholder-abcdefghijklmnopqrstuvwxyz",
        components={"paper": paper.model_dump(mode="json")},
    )

    original_import_runtime_modules = views._import_runtime_modules

    def patched_import_runtime_modules():
        mods = original_import_runtime_modules()
        mods["GEOParser"] = _StubGEOParser
        mods["SRAParser"] = _StubSRAParser
        return mods

    monkeypatch.setattr(views, "_import_runtime_modules", patched_import_runtime_modules)

    views._run_step(job_id, "datasets")
    job = get_job(job_id)
    assert job is not None
    datasets = (job.get("components") or {}).get("datasets") or []
    accessions = {d.get("accession") for d in datasets}
    assert "GSE314176" in accessions
    assert "SRP123456" in accessions


def test_portal_dataset_step_adds_placeholder_when_no_accessions_found(monkeypatch):
    paper = Paper(
        title="No datasets paper",
        pmid="99999999",
        source=PaperSource.PMID,
        source_path="99999999",
        sections=[],
        raw_text="",
    )
    job_id = create_job(
        input_type="pmid",
        input_value="99999999",
        source="99999999",
        source_type="pmid",
        llm_model="gpt-5.4",
        llm_api_key="sk-test-placeholder-abcdefghijklmnopqrstuvwxyz",
        components={"paper": paper.model_dump(mode="json"), "method": {}},
    )

    original_import_runtime_modules = views._import_runtime_modules

    def patched_import_runtime_modules():
        mods = original_import_runtime_modules()
        mods["GEOParser"] = _StubGEOParser
        mods["SRAParser"] = _StubSRAParser
        return mods

    monkeypatch.setattr(views, "_import_runtime_modules", patched_import_runtime_modules)

    views._run_step(job_id, "datasets")
    job = get_job(job_id)
    assert job is not None
    datasets = (job.get("components") or {}).get("datasets") or []
    assert len(datasets) == 1
    row = datasets[0]
    assert row.get("accession") == "NO_DATASET_REPORTED"
    assert row.get("source") == "other"
    assert bool((row.get("raw_metadata") or {}).get("placeholder")) is True
