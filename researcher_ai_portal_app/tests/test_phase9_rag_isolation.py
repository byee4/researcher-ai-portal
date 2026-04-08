from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from researcher_ai.models.paper import Paper, PaperSource

from researcher_ai_portal_app import views
from researcher_ai_portal_app.job_store import create_job


class _StubMethod:
    def model_dump(self, mode: str = "json"):
        return {"assay_graph": {"assays": [], "dependencies": []}, "parse_warnings": []}


def _make_job() -> str:
    paper = Paper(title="RAG test", source=PaperSource.PMID, source_path="123", pmid="123")
    return create_job(
        input_type="pmid",
        input_value="123",
        source="123",
        source_type="pmid",
        llm_model="gpt-5.4",
        llm_api_key="sk-12345678901234567890",
        components={"paper": paper.model_dump(mode="json"), "figures": []},
    )


def test_methods_step_uses_per_job_rag_dirs_and_cleans_them(monkeypatch, tmp_path: Path):
    seen_dirs: list[str] = []

    class _StubMethodsParser:
        def __init__(self, llm_model: str = "", rag_persist_dir: str | None = None, **kwargs):
            assert rag_persist_dir
            seen_dirs.append(str(rag_persist_dir))
            self.rag_persist_dir = str(rag_persist_dir)

        def parse(self, paper, figures=None, computational_only=True):
            marker = Path(self.rag_persist_dir) / "marker.txt"
            marker.write_text("ok", encoding="utf-8")
            return _StubMethod()

    original_import_runtime_modules = views._import_runtime_modules

    def patched_import_runtime_modules():
        mods = original_import_runtime_modules()
        mods["MethodsParser"] = _StubMethodsParser
        return mods

    monkeypatch.setattr(views, "_import_runtime_modules", patched_import_runtime_modules)
    monkeypatch.setenv("RESEARCHER_AI_RAG_MODE", "per_job")
    monkeypatch.setenv("RESEARCHER_AI_RAG_BASE_DIR", str(tmp_path))

    jobs = [_make_job(), _make_job()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(views._run_step, job_id, "method") for job_id in jobs]
        for future in futures:
            future.result()

    assert len(seen_dirs) == 2
    assert len(set(seen_dirs)) == 2
    for rag_dir in seen_dirs:
        assert str(tmp_path) in rag_dir
        assert not Path(rag_dir).exists(), f"expected temporary RAG dir cleanup: {rag_dir}"


def test_methods_step_shared_rag_mode_uses_configured_dir(monkeypatch, tmp_path: Path):
    shared_dir = tmp_path / "shared-rag"

    class _StubMethodsParser:
        def __init__(self, llm_model: str = "", rag_persist_dir: str | None = None, **kwargs):
            self.rag_persist_dir = rag_persist_dir

        def parse(self, paper, figures=None, computational_only=True):
            assert self.rag_persist_dir == str(shared_dir)
            return _StubMethod()

    original_import_runtime_modules = views._import_runtime_modules

    def patched_import_runtime_modules():
        mods = original_import_runtime_modules()
        mods["MethodsParser"] = _StubMethodsParser
        return mods

    monkeypatch.setattr(views, "_import_runtime_modules", patched_import_runtime_modules)
    monkeypatch.setenv("RESEARCHER_AI_RAG_MODE", "shared")
    monkeypatch.setenv("RESEARCHER_AI_RAG_BASE_DIR", str(shared_dir))

    views._run_step(_make_job(), "method")
    assert shared_dir.exists()
