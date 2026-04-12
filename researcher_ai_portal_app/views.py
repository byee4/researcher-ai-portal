from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import concurrent.futures
import base64
import hashlib
from datetime import datetime, timezone
from html import unescape
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlparse
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .confidence import compute_actionable_items, compute_confidence
from .dag_app import build_dag_app
from .dashboards import build_dashboard_app
from .forms import ComponentJSONForm, FigureGroundTruthForm, MethodStepCorrectionForm
from .job_events import append_job_log, merge_logs
from .models import PaperCache, WorkflowJob
from .job_store import create_job, get_job, update_job


DJANGO_ROOT = Path(__file__).resolve().parents[1]
RESULT_DIR = DJANGO_ROOT / "parse_results"
PDF_STAGE_DIR = RESULT_DIR / "uploaded_pdfs"

STEP_ORDER = ["paper", "figures", "method", "datasets", "software", "pipeline"]
STEP_LABELS = {
    "paper": "Paper Parser",
    "figures": "Figure Parser",
    "method": "Methods Parser",
    "datasets": "Dataset Parsers",
    "software": "Software Parser",
    "pipeline": "Pipeline Builder",
}
STEP_DEPENDENCIES = {
    "paper": [],
    "figures": ["paper"],
    "method": ["paper", "figures"],
    "datasets": ["paper", "method"],
    "software": ["method"],
    "pipeline": ["method", "datasets", "software", "figures"],
}
COMMON_LLM_MODELS = [
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5.2",
    "o4-mini",
    "gemini-3.1-pro",
    "gemini-2.5-pro",
    "claude-sonnet-4-6",
    "claude-opus-4-1",
]
_ACC_RE = re.compile(
    r"\b("
    r"GSE\d{4,8}|GSM\d{4,8}|GDS\d{3,7}|GPL\d{3,7}|"
    r"SRP\d{4,9}|SRX\d{4,9}|SRR\d{4,9}|ERP\d{4,9}|ERR\d{4,9}|"
    r"PRJNA\d{4,9}|PRJEB\d{4,9}"
    r")\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s<>'\"()]+", re.IGNORECASE)
_HTML_IMG_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)
_IMAGE_KEYWORDS = ("image", "url", "src", "href", "thumbnail")
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".tif", ".tiff", ".bmp", ".avif")
_SUPP_FIG_RE = re.compile(r"\b(supplementary|supp\.?|extended\s+data)\b", re.IGNORECASE)
_BLOCKED_IMG_HINTS = ("us_flag.svg", "/static/img/us_flag", "pmc-cloudpmc-viewer")
_PMC_FETCH_HEADERS = {
    "User-Agent": "researcher-ai-portal/figure-proxy (+https://pmc.ncbi.nlm.nih.gov/)",
    "Referer": "https://pmc.ncbi.nlm.nih.gov/",
}
_PRIMARY_FIG_ID_RE = re.compile(r"(?i)\b(?:figure|fig\.?)\s*(\d{1,3})\b")
_SUPP_FIG_ID_RE = re.compile(r"(?i)\b(?:supplementary|supp\.?)\s*(?:figure|fig\.?)\s*(?:s)?(\d{1,3})\b")
_EXT_DATA_FIG_ID_RE = re.compile(r"(?i)\bextended\s+data\s+(?:figure|fig\.?)\s*(\d{1,3})\b")
_SUPP_SHORT_FIG_ID_RE = re.compile(r"(?i)\b(?:fig(?:ure)?\.?|f)\s*s\s*(\d{1,3})\b")
_VISION_FALLBACK_WARN_RE = re.compile(
    r"paper_rag_vision_fallback:\s*count\s*=\s*(\d+)\s+latency_seconds\s*=\s*([0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE | re.DOTALL,
)
_BIOWORKFLOW_BLOCKED_RE = re.compile(r"bioworkflow_blocked:.*?ungrounded_fields\s*=\s*(\d+)", re.IGNORECASE)
_RAG_RETRIEVAL_ROUNDS_RE = re.compile(r"retrieval(?:_refinement)?[_\s-]*rounds?\s*[:=]\s*(\d+)", re.IGNORECASE)
_RAG_RETRIEVED_CHUNKS_RE = re.compile(
    r"(?:retrieved|retrieval)[_\s-]*(?:chunks?|docs?|documents?)\s*[:=]\s*(\d+)",
    re.IGNORECASE,
)
_RAG_CONTEXT_TOKENS_RE = re.compile(
    r"(?:total[_\s-]*)?context[_\s-]*tokens?(?:_est)?\s*[:=]\s*(\d+)",
    re.IGNORECASE,
)
_LLM_ENV_LOCK = threading.RLock()
_SESSION_LLM_API_KEY_FIELD = "llm_api_key_enc"
_FIGURE_PROXY_CACHE_DIR = Path(settings.MEDIA_ROOT) / "figure_proxy_cache"
_FIGURE_PROXY_CACHE_TTL_SEC = max(60, int(os.environ.get("FIGURE_PROXY_CACHE_TTL_SEC", "604800") or "604800"))
_ORCHESTRATOR_META_MAX_STRING_LEN = 2000
_ORCHESTRATOR_META_MAX_LIST_LEN = 100
_ORCHESTRATOR_META_MAX_DEPTH = 6
_STUCK_JOB_TIMEOUT_SECONDS = 3600


def _log_job_event(job_id: str, message: str, *, step: str = "", level: str = "info") -> None:
    try:
        append_job_log(job_id, message, step=step, level=level)
    except Exception:
        # Best-effort logging only; never break parsing on diagnostics.
        return


def invalidated_steps(job: dict[str, Any], edited_step: str) -> list[str]:
    """Return downstream steps invalidated by a user edit."""
    if edited_step not in STEP_ORDER:
        return []
    dirty = {edited_step}
    for step in STEP_ORDER:
        if step == edited_step:
            continue
        if dirty & set(STEP_DEPENDENCIES.get(step, [])):
            dirty.add(step)
    idx = STEP_ORDER.index(edited_step)
    return [s for s in STEP_ORDER[idx + 1 :] if s in dirty]


def _infer_provider(model: str) -> str:
    m = (model or "").strip().lower()
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gemini"):
        return "gemini"
    if (
        m.startswith("gpt")
        or m.startswith("chatgpt")
        or m.startswith("o1")
        or m.startswith("o3")
        or m.startswith("o4")
    ):
        return "openai"
    return "anthropic"


def _validate_llm_model(model: str) -> str:
    value = (model or "").strip()
    if not value:
        raise ValueError("LLM model is required.")
    return value


def _validate_llm_api_key(api_key: str, provider: str) -> str:
    value = (api_key or "").strip()
    if not value:
        raise ValueError("LLM API key is required.")
    if provider == "anthropic" and not value.startswith("sk-ant-"):
        raise ValueError("Invalid Anthropic API key format. Expected 'sk-ant-...'.")
    if provider == "openai" and not (value.startswith("sk-") or value.startswith("sk-proj-")):
        raise ValueError("Invalid OpenAI API key format. Expected 'sk-' or 'sk-proj-...'.")
    if provider == "gemini" and not re.fullmatch(r"[A-Za-z0-9_-]{39}", value):
        raise ValueError("Invalid Gemini API key format. Expected 39 URL-safe characters.")
    if len(value) < 20:
        raise ValueError("API key appears too short.")
    return value


def _import_runtime_modules() -> dict[str, Any]:
    from researcher_ai.models.paper import Paper, PaperSource
    from researcher_ai.models.figure import Figure
    from researcher_ai.models.method import Method
    from researcher_ai.models.dataset import Dataset
    from researcher_ai.models.software import Software
    from researcher_ai.models.pipeline import Pipeline
    from researcher_ai.parsers.paper_parser import PaperParser
    from researcher_ai.parsers.figure_parser import FigureParser
    from researcher_ai.parsers.methods_parser import MethodsParser
    from researcher_ai.parsers.software_parser import SoftwareParser
    from researcher_ai.parsers.data.geo_parser import GEOParser
    from researcher_ai.parsers.data.sra_parser import SRAParser
    from researcher_ai.pipeline.builder import PipelineBuilder

    return {
        "Paper": Paper,
        "PaperSource": PaperSource,
        "Figure": Figure,
        "Method": Method,
        "Dataset": Dataset,
        "Software": Software,
        "Pipeline": Pipeline,
        "PaperParser": PaperParser,
        "FigureParser": FigureParser,
        "MethodsParser": MethodsParser,
        "SoftwareParser": SoftwareParser,
        "GEOParser": GEOParser,
        "SRAParser": SRAParser,
        "PipelineBuilder": PipelineBuilder,
    }


def _session_fernet() -> Fernet:
    raw = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def _encrypt_session_secret(value: str) -> str:
    if not value:
        return ""
    return _session_fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt_session_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _session_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return ""


def _session_llm_api_key(request) -> str:
    encrypted = str(request.session.get(_SESSION_LLM_API_KEY_FIELD, "") or "")
    if encrypted:
        return _decrypt_session_secret(encrypted)
    # Backward-compat for older sessions populated before encryption rollout.
    legacy = str(request.session.get("llm_api_key", "") or "")
    return legacy


def _canonical_paper_cache_id(source_type: str, source: str) -> str:
    return f"{(source_type or '').strip().lower()}:{(source or '').strip()}"


def _stage_uploaded_pdf(uploaded_file) -> Path:
    """Persist an uploaded PDF to a durable portal-managed staging path."""
    PDF_STAGE_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(str(getattr(uploaded_file, "name", "") or "")).suffix.lower()
    if suffix != ".pdf":
        suffix = ".pdf"
    staged_path = (PDF_STAGE_DIR / f"{uuid4().hex}{suffix}").resolve()
    with staged_path.open("wb") as fh:
        for chunk in uploaded_file.chunks():
            fh.write(chunk)
    return staged_path


@contextmanager
def _llm_env(job: dict[str, Any]):
    provider = _infer_provider(job.get("llm_model", ""))
    old_openai = os.environ.get("OPENAI_API_KEY")
    old_anthropic = os.environ.get("ANTHROPIC_API_KEY")
    old_gemini = os.environ.get("GEMINI_API_KEY")
    old_model = os.environ.get("RESEARCHER_AI_MODEL")
    with _LLM_ENV_LOCK:
        try:
            os.environ["RESEARCHER_AI_MODEL"] = job.get("llm_model", "")
            if provider == "openai":
                os.environ["OPENAI_API_KEY"] = job.get("llm_api_key", "")
            elif provider == "gemini":
                os.environ["GEMINI_API_KEY"] = job.get("llm_api_key", "")
            else:
                os.environ["ANTHROPIC_API_KEY"] = job.get("llm_api_key", "")
            yield
        finally:
            if old_openai is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = old_openai
            if old_anthropic is None:
                os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                os.environ["ANTHROPIC_API_KEY"] = old_anthropic
            if old_gemini is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = old_gemini
            if old_model is None:
                os.environ.pop("RESEARCHER_AI_MODEL", None)
            else:
                os.environ["RESEARCHER_AI_MODEL"] = old_model


def _collect_accessions(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _ACC_RE.finditer(text or ""):
        acc = m.group(1).upper()
        if acc not in seen:
            seen.add(acc)
            out.append(acc)
    return out


def _component_status(step: str, payload: Any) -> tuple[str, list[str]]:
    missing: list[str] = []
    if payload is None:
        return "missing", [f"{step}: no output"]
    if isinstance(payload, list) and not payload:
        return "missing", [f"{step}: empty list"]
    if isinstance(payload, dict):
        if step == "paper":
            for key in ("title", "sections"):
                if not payload.get(key):
                    missing.append(key)
        if step == "method":
            graph = payload.get("assay_graph") or {}
            if not graph.get("assays"):
                missing.append("assay_graph.assays")
        if step == "pipeline":
            config = payload.get("config") or {}
            if not config.get("steps"):
                missing.append("config.steps")
        warnings = payload.get("parse_warnings") or []
        if warnings:
            return "inferred", missing + [f"parse_warnings={len(warnings)}"]
    text = json.dumps(payload, ensure_ascii=False).lower()
    if "could not be parsed" in text or "unknown" in text:
        return "inferred", missing
    if missing:
        return "inferred", missing
    return "found", []


def _parse_vision_fallback_warning(warning: Any) -> dict[str, Any] | None:
    text = " ".join(str(warning or "").split())
    if not text:
        return None
    match = _VISION_FALLBACK_WARN_RE.search(text)
    if not match:
        return None
    try:
        return {
            "vision_fallback_count": int(match.group(1)),
            "vision_fallback_latency_seconds": float(match.group(2)),
        }
    except (TypeError, ValueError):
        return None


def _derive_human_review_summary_from_warnings(parse_warnings: list[Any]) -> dict[str, Any] | None:
    for warning in parse_warnings:
        text = " ".join(str(warning or "").split())
        match = _BIOWORKFLOW_BLOCKED_RE.search(text)
        if not match:
            continue
        ungrounded_count = int(match.group(1))
        return {
            "reason": "validation_blocked",
            "ungrounded_count": ungrounded_count,
            "ungrounded_fields": [],
            "recommended_action": (
                "Provide missing parameters manually or switch "
                "RESEARCHER_AI_BIOWORKFLOW_MODE=warn to continue with flagged defaults."
            ),
        }
    return None


def _extract_method_diagnostics(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    out: dict[str, Any] = {}
    parse_warnings = list(payload.get("parse_warnings") or [])

    total_fallback_count = 0
    total_fallback_latency = 0.0
    for warning in parse_warnings:
        parsed = _parse_vision_fallback_warning(warning)
        if parsed is None:
            continue
        total_fallback_count += int(parsed["vision_fallback_count"])
        total_fallback_latency += float(parsed["vision_fallback_latency_seconds"])
    if total_fallback_count > 0:
        out["vision_fallback_count"] = total_fallback_count
        out["vision_fallback_latency_seconds"] = round(total_fallback_latency, 3)

    explicit_review_required = bool(payload.get("human_review_required", False))
    explicit_review_summary = payload.get("human_review_summary")
    derived_review_summary = _derive_human_review_summary_from_warnings(parse_warnings)
    review_required = explicit_review_required or derived_review_summary is not None
    if review_required:
        out["human_review_required"] = True
    if isinstance(explicit_review_summary, dict):
        out["human_review_summary"] = explicit_review_summary
    elif isinstance(derived_review_summary, dict):
        out["human_review_summary"] = derived_review_summary

    return out


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rag_phase_for_message(message: str) -> str:
    text = str(message or "").strip().lower()
    if not text:
        return "post_parse_validation"
    if "indexing" in text or "rag index" in text:
        return "indexing"
    if "retriev" in text or "context" in text:
        return "retrieval"
    if "prompt" in text:
        return "prompt_assembly"
    if "llm" in text or "sending methods extraction request" in text or "response received" in text:
        return "generation"
    return "post_parse_validation"


def _extract_retrieval_metrics(parse_warnings: list[Any], parse_logs: list[dict[str, Any]]) -> dict[str, Any]:
    retrieval_rounds: int | None = None
    retrieved_chunk_count: int | None = None
    total_context_tokens_est: int | None = None

    texts: list[str] = [str(w or "") for w in parse_warnings]
    for entry in parse_logs:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("step") or "") != "method":
            continue
        texts.append(str(entry.get("message") or ""))

    for text in texts:
        for rx, key in (
            (_RAG_RETRIEVAL_ROUNDS_RE, "retrieval_rounds"),
            (_RAG_RETRIEVED_CHUNKS_RE, "retrieved_chunk_count"),
            (_RAG_CONTEXT_TOKENS_RE, "total_context_tokens_est"),
        ):
            match = rx.search(text)
            if not match:
                continue
            val = _safe_int(match.group(1), 0)
            if key == "retrieval_rounds":
                retrieval_rounds = val
            elif key == "retrieved_chunk_count":
                retrieved_chunk_count = val
            elif key == "total_context_tokens_est":
                total_context_tokens_est = val

    return {
        "rounds": retrieval_rounds,
        "retrieved_chunk_count": retrieved_chunk_count,
        "total_context_tokens_est": total_context_tokens_est,
    }


def _timeline_event_from_log(entry: dict[str, Any]) -> dict[str, Any]:
    message = str(entry.get("message") or "")
    return {
        "ts": str(entry.get("ts") or _iso_utc_now()),
        "phase": _rag_phase_for_message(message),
        "level": str(entry.get("level") or "info"),
        "message": message,
        "source": "parse_log",
    }


def build_rag_workflow_payload(job: dict[str, Any]) -> dict[str, Any]:
    """Normalize RAG workflow metadata + method logs into a stable payload."""
    metadata = dict(job.get("job_metadata") or {})
    rag = metadata.get("rag_workflow") if isinstance(metadata.get("rag_workflow"), dict) else {}
    components = job.get("components") or {}
    method_payload = components.get("method") if isinstance(components.get("method"), dict) else {}
    parse_warnings = list((method_payload or {}).get("parse_warnings") or [])
    method_logs = [
        x for x in (job.get("parse_logs") or [])
        if isinstance(x, dict) and str(x.get("step") or "") == "method"
    ]

    events = [
        {
            "ts": str(ev.get("ts") or _iso_utc_now()),
            "phase": str(ev.get("phase") or "post_parse_validation"),
            "level": str(ev.get("level") or "info"),
            "message": str(ev.get("message") or ""),
            "source": "telemetry",
        }
        for ev in (rag.get("events") or [])
        if isinstance(ev, dict)
    ]
    timeline = [*events, *[_timeline_event_from_log(e) for e in method_logs]]
    timeline.sort(key=lambda row: str(row.get("ts") or ""))

    assays = ((method_payload or {}).get("assay_graph") or {}).get("assays") or []
    diagnostics = _extract_method_diagnostics(method_payload)
    retrieval = dict(rag.get("retrieval") or {})
    inferred_retrieval = _extract_retrieval_metrics(parse_warnings, method_logs)
    retrieval.setdefault("rounds", inferred_retrieval.get("rounds"))
    retrieval.setdefault("retrieved_chunk_count", inferred_retrieval.get("retrieved_chunk_count"))
    retrieval.setdefault("total_context_tokens_est", inferred_retrieval.get("total_context_tokens_est"))

    result = dict(rag.get("result") or {})
    result.setdefault("assay_count", len(assays))
    result.setdefault("parse_warning_count", len(parse_warnings))
    result.setdefault(
        "review_required",
        bool(result.get("review_required"))
        or bool(diagnostics.get("human_review_required"))
        or bool(metadata.get("human_review_required")),
    )

    out = {
        "mode": str(rag.get("mode") or "per_job"),
        "indexing": {
            "section_count": _safe_int((rag.get("indexing") or {}).get("section_count"), 0),
            "figure_caption_count": _safe_int((rag.get("indexing") or {}).get("figure_caption_count"), 0),
            "started_at": (rag.get("indexing") or {}).get("started_at"),
            "finished_at": (rag.get("indexing") or {}).get("finished_at"),
            "duration_s": _safe_float((rag.get("indexing") or {}).get("duration_s"), None),
        },
        "retrieval": {
            "rounds": retrieval.get("rounds"),
            "retrieved_chunk_count": retrieval.get("retrieved_chunk_count"),
            "total_context_tokens_est": retrieval.get("total_context_tokens_est"),
        },
        "generation": {
            "model": str((rag.get("generation") or {}).get("model") or (job.get("llm_model") or "")),
            "started_at": (rag.get("generation") or {}).get("started_at"),
            "finished_at": (rag.get("generation") or {}).get("finished_at"),
            "duration_s": _safe_float((rag.get("generation") or {}).get("duration_s"), None),
        },
        "result": result,
        "events": events,
        "timeline": timeline,
        "diagnostics": diagnostics,
        "has_telemetry": bool(rag),
    }
    return out


def _validate_component_json(step: str, payload: Any, mods: dict[str, Any]) -> Any:
    if step == "paper":
        return mods["Paper"].model_validate(payload).model_dump(mode="json")
    if step == "figures":
        return [mods["Figure"].model_validate(x).model_dump(mode="json") for x in (payload or [])]
    if step == "method":
        return mods["Method"].model_validate(payload).model_dump(mode="json")
    if step == "datasets":
        out: list[dict[str, Any]] = []
        for item in (payload or []):
            validated = mods["Dataset"].model_validate(item).model_dump(mode="json")
            # Preserve subtype-specific dataset keys while enforcing base Dataset schema.
            if isinstance(item, dict):
                merged = dict(item)
                merged.update(validated)
                out.append(merged)
            else:
                out.append(validated)
        return out
    if step == "software":
        return [mods["Software"].model_validate(x).model_dump(mode="json") for x in (payload or [])]
    if step == "pipeline":
        return mods["Pipeline"].model_validate(payload).model_dump(mode="json")
    raise ValueError(f"Unknown step {step}")


def _default_plot_category_for_type(plot_type: str) -> str:
    pt = (plot_type or "").strip().lower()
    mapping = {
        "bar": "categorical",
        "stacked_bar": "categorical",
        "grouped_bar": "categorical",
        "box": "categorical",
        "violin": "categorical",
        "scatter": "relational",
        "line": "relational",
        "bubble": "relational",
        "heatmap": "matrix",
        "volcano": "genomic",
        "tsne": "dimensionality",
        "umap": "dimensionality",
        "venn": "flow",
        "upset": "flow",
        "image": "image",
    }
    return mapping.get(pt, "composite")


def _figure_uncertainty_rows(figures_payload: Any) -> list[dict[str, Any]]:
    """Summarize likely uncertain figure parses for UI triage."""
    rows: list[dict[str, Any]] = []
    if not isinstance(figures_payload, list):
        return rows
    for fig in figures_payload:
        if not isinstance(fig, dict):
            continue
        figure_id = str(fig.get("figure_id") or "Unknown Figure")
        reasons: list[str] = []
        title = str(fig.get("title") or "").strip()
        caption = str(fig.get("caption") or "").strip()
        purpose = str(fig.get("purpose") or "").lower()
        subfigs = fig.get("subfigures") or []
        if not title:
            reasons.append("missing_title")
        if not caption:
            reasons.append("missing_caption")
        if "could not be parsed" in purpose:
            reasons.append("figure_unparsed")
        if not subfigs:
            reasons.append("no_subfigures")
        for sf in subfigs:
            if not isinstance(sf, dict):
                continue
            label = str(sf.get("label") or "?")
            pt = str(sf.get("plot_type") or "").strip().lower()
            raw_comp = sf.get("composite_confidence")
            if raw_comp is None:
                conf = float(sf.get("classification_confidence", 0.5) or 0.5) * 100.0
            else:
                conf = float(raw_comp or 0.0)
            if pt in ("", "other"):
                reasons.append(f"panel_{label}:unknown_plot_type")
            if conf < 65.0:
                reasons.append(f"panel_{label}:low_confidence")
            x_axis = sf.get("x_axis") if isinstance(sf.get("x_axis"), dict) else {}
            y_axis = sf.get("y_axis") if isinstance(sf.get("y_axis"), dict) else {}
            if pt not in ("image", "venn", "upset") and not x_axis.get("label"):
                reasons.append(f"panel_{label}:missing_x_axis")
            if pt not in ("image", "venn", "upset") and not y_axis.get("label"):
                reasons.append(f"panel_{label}:missing_y_axis")
        dedup: list[str] = []
        seen: set[str] = set()
        for reason in reasons:
            if reason not in seen:
                seen.add(reason)
                dedup.append(reason)
        if dedup:
            rows.append({"figure_id": figure_id, "reasons": dedup})
    return sorted(rows, key=lambda row: _alphanumeric_sort_key(str(row.get("figure_id") or "")))


def _figure_provenance_rows(figures_payload: Any) -> list[dict[str, Any]]:
    """Summarize calibration provenance by figure/panel for UI display."""
    rows: list[dict[str, Any]] = []
    if not isinstance(figures_payload, list):
        return rows
    for fig in figures_payload:
        if not isinstance(fig, dict):
            continue
        figure_id = str(fig.get("figure_id") or "Unknown Figure")
        panel_rows: list[dict[str, Any]] = []
        for sf in (fig.get("subfigures") or []):
            if not isinstance(sf, dict):
                continue
            label = str(sf.get("label") or "?")
            pt = str(sf.get("plot_type") or "other")
            raw_comp = sf.get("composite_confidence")
            if raw_comp is None:
                composite_conf = float(sf.get("classification_confidence", 0.0) or 0.0) * 100.0
            else:
                composite_conf = float(raw_comp or 0.0)
            conf_scores = sf.get("confidence_scores") if isinstance(sf.get("confidence_scores"), dict) else {}
            bbox = sf.get("boundary_box") if isinstance(sf.get("boundary_box"), dict) else {}
            evidence = [e for e in (sf.get("evidence_spans") or []) if isinstance(e, str) and e.strip()]
            calib_tags = [e for e in evidence if e.startswith("calibration_rule:")]
            gt_tags = [e for e in evidence if e.startswith("ground_truth")]
            if not calib_tags and not gt_tags and not conf_scores and not bbox:
                continue
            panel_rows.append(
                {
                    "label": label,
                    "plot_type": pt,
                    "confidence": round(composite_conf, 1),
                    "confidence_scores": conf_scores,
                    "boundary_box": bbox,
                    "calibration_rules": calib_tags,
                    "ground_truth_tags": gt_tags,
                }
            )
        if panel_rows:
            rows.append({"figure_id": figure_id, "panels": panel_rows})
    return sorted(rows, key=lambda row: _alphanumeric_sort_key(str(row.get("figure_id") or "")))


def _figure_merged_rows(
    media_rows: list[dict[str, Any]],
    uncertainty_rows: list[dict[str, Any]],
    provenance_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge the three figure data sources into one unified list.

    Each row has:
        figure_id, figure_key, title, caption, entries, deferred_parser,
        is_uncertain, min_confidence, panels[]
    Each panel has:
        label, plot_type, confidence, is_uncertain, issue_tags,
        confidence_scores, calibration_rules, ground_truth_tags
    """
    # Build lookup dicts keyed by canonical figure reference.
    uncertainty_map: dict[str, list[str]] = {}
    for row in uncertainty_rows:
        key, _ = _canonical_figure_reference(str(row.get("figure_id") or ""))
        if not key:
            continue
        uncertainty_map.setdefault(key, []).extend(row.get("reasons") or [])

    provenance_map: dict[str, list[dict[str, Any]]] = {}
    for row in provenance_rows:
        key, _ = _canonical_figure_reference(str(row.get("figure_id") or ""))
        if not key:
            continue
        provenance_map.setdefault(key, []).extend(row.get("panels") or [])

    merged: list[dict[str, Any]] = []
    emitted: set[str] = set()
    for media in media_rows:
        fid = str(media.get("figure_id") or "")
        key, display = _canonical_figure_reference(fid)
        if not key or key in emitted:
            continue
        emitted.add(key)
        reasons = uncertainty_map.get(key, [])
        raw_panels = provenance_map.get(key, [])

        # Per-panel: tag which issues belong to each panel label
        panels: list[dict[str, Any]] = []
        for p in raw_panels:
            label = p["label"]
            panel_reasons = [
                r.split(":", 1)[1]
                for r in reasons
                if r.startswith(f"panel_{label}:")
            ]
            panels.append({
                **p,
                "is_uncertain": bool(panel_reasons),
                "issue_tags": panel_reasons,
            })

        # Sort panels: uncertain first, then by confidence ascending
        panels.sort(key=lambda p: (not p["is_uncertain"], p["confidence"]))

        min_conf = min((p["confidence"] for p in panels), default=100.0)
        is_uncertain = bool(reasons)

        merged.append({
            **media,
            "figure_id": display or fid,
            "figure_key": _figure_id_key(display or fid),
            "is_uncertain": is_uncertain,
            "uncertainty_reasons": reasons,
            "panels": panels,
            "min_confidence": min_conf,
        })

    # Keep figure ordering stable and predictable across all views.
    merged.sort(key=lambda r: _alphanumeric_sort_key(str(r.get("figure_id") or "")))
    return merged


def _normalize_url(value: str) -> str:
    url = unescape((value or "").strip())
    while url and url[-1] in ".,;)]}>":
        url = url[:-1]
    return url


def _urls_from_text(value: str) -> list[str]:
    return [_normalize_url(m.group(0)) for m in _URL_RE.finditer(value or "")]


def _extract_figure_image_urls(fig: dict[str, Any]) -> list[str]:
    found: list[str] = []

    def walk(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
            return
        if isinstance(value, list):
            for item in value:
                walk(item, key)
            return
        if not isinstance(value, str):
            return

        key_l = key.lower()
        text = value.strip()
        if any(token in key_l for token in _IMAGE_KEYWORDS):
            if text.startswith(("http://", "https://")):
                found.append(_normalize_url(text))
            for extracted in _urls_from_text(text):
                found.append(extracted)
        elif key_l in ("caption", "purpose", "title", "description"):
            for extracted in _urls_from_text(text):
                found.append(extracted)

    walk(fig)

    deduped: list[str] = []
    seen: set[str] = set()
    for url in found:
        if not url or not url.lower().startswith(("http://", "https://")):
            continue
        key = url.casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(url)
    return deduped


def _looks_like_image_url(url: str) -> bool:
    lower = (url or "").lower()
    if any(sfx in lower for sfx in _IMAGE_SUFFIXES):
        return True
    return any(token in lower for token in ("/figure/", "/figures/", "image", "render", "download"))


def _is_supplementary_figure_id(figure_id: str) -> bool:
    return bool(_SUPP_FIG_RE.search(figure_id or ""))


def _primary_figure_number(figure_id: str) -> str | None:
    if _is_supplementary_figure_id(figure_id):
        return None
    match = re.search(r"(?i)\b(?:f|fig(?:ure)?\.?)\s*(\d+)", figure_id or "")
    if not match:
        return None
    return match.group(1)


def _split_primary_and_supplementary_figure_ids(figure_ids: list[str]) -> tuple[list[str], list[str]]:
    primary: list[str] = []
    supplementary: list[str] = []
    for fig_id in figure_ids or []:
        if _is_supplementary_figure_id(fig_id):
            supplementary.append(fig_id)
        else:
            primary.append(fig_id)
    return primary, supplementary


def _canonical_figure_reference(figure_id: str) -> tuple[str, str]:
    """Return a canonical (dedup_key, display_label) tuple for figure references.

    Dedup keys intentionally keep primary and supplementary references separate:
    - Figure 1 / Fig. 1 / F1              -> primary:1
    - Figure S1 / Supplementary Figure 1  -> supplementary:1
    - Extended Data Figure 1              -> extended_data:1
    """
    raw = str(figure_id or "").strip()
    if not raw:
        return ("", "")

    ext_match = _EXT_DATA_FIG_ID_RE.search(raw)
    if ext_match:
        number = int(ext_match.group(1))
        return (f"extended_data:{number}", f"Extended Data Figure {number}")

    is_supp = _is_supplementary_figure_id(raw)
    supp_match = _SUPP_FIG_ID_RE.search(raw)
    if supp_match:
        number = int(supp_match.group(1))
        return (f"supplementary:{number}", f"Supplementary Figure {number}")
    short = _SUPP_SHORT_FIG_ID_RE.search(raw)
    if short:
        number = int(short.group(1))
        return (f"supplementary:{number}", f"Supplementary Figure {number}")

    primary = _primary_figure_number(raw)
    if primary:
        number = int(primary)
        return (f"primary:{number}", f"Figure {number}")

    # Fallback for non-standard labels where no figure number is detectable.
    key = _figure_id_key(raw) or raw.casefold()
    return (f"text:{key}", raw)


def _collapse_figure_reference_variants(figure_ids: list[str]) -> list[str]:
    """Collapse equivalent figure-id aliases into one canonical label.

    This runs before parser interpretation to avoid duplicate figure parsing for
    aliases like Figure 1 / Fig. 1 / F1. Supplementary and primary references
    remain distinct by design.
    """
    collapsed: list[str] = []
    seen: set[str] = set()
    for fid in figure_ids or []:
        key, display = _canonical_figure_reference(str(fid or ""))
        if not key or not display or key in seen:
            continue
        seen.add(key)
        collapsed.append(display)
    return collapsed


def _infer_figure_ids_from_paper_text(paper: Any) -> list[str]:
    """Infer figure ids from paper text when parser-produced ids are absent."""
    parts: list[str] = []
    raw_text = getattr(paper, "raw_text", "") or ""
    if raw_text:
        parts.append(str(raw_text))
    for sec in (getattr(paper, "sections", None) or []):
        text = getattr(sec, "text", "") or ""
        if text:
            parts.append(str(text))
    joined = "\n".join(parts)
    if not joined.strip():
        return []

    out: list[str] = []
    seen: set[str] = set()

    def add(label: str) -> None:
        key = label.lower()
        if key not in seen:
            seen.add(key)
            out.append(label)

    for m in _PRIMARY_FIG_ID_RE.finditer(joined):
        add(f"Figure {int(m.group(1))}")
    for m in _SUPP_FIG_ID_RE.finditer(joined):
        add(f"Supplementary Figure {int(m.group(1))}")
    for m in _EXT_DATA_FIG_ID_RE.finditer(joined):
        add(f"Extended Data Figure {int(m.group(1))}")

    return out


def _figure_id_key(figure_id: str) -> str:
    text = (figure_id or "").strip().lower()
    text = re.sub(r"(?i)\bfig(?:ure)?\.?\b", "figure", text)
    text = re.sub(r"(?i)\bf(?=\s*\d)", "figure", text)
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _alphanumeric_sort_key(value: str) -> tuple[tuple[int, Any], ...]:
    text = str(value or "").strip().lower()
    text = re.sub(r"(?i)\bfig(?:ure)?\.?\b", "figure", text)
    text = re.sub(r"(?i)\bf(?=\s*\d)", "figure", text)
    text = re.sub(r"\s+", " ", text)
    parts = re.findall(r"\d+|[a-z]+", text)
    key: list[tuple[int, Any]] = []
    for token in parts:
        if token.isdigit():
            key.append((1, int(token)))
        else:
            key.append((0, token))
    # Add normalized text as a stable tie-breaker.
    key.append((2, text))
    return tuple(key)


def _sort_figures_and_panels_alphanumerically(figures_payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    for fig in figures_payload or []:
        if not isinstance(fig, dict):
            continue
        fig_copy = dict(fig)
        subfigures = fig_copy.get("subfigures")
        if isinstance(subfigures, list):
            fig_copy["subfigures"] = sorted(
                [dict(sf) for sf in subfigures if isinstance(sf, dict)],
                key=lambda sf: _alphanumeric_sort_key(str(sf.get("label") or "")),
            )
        ordered.append(fig_copy)
    return sorted(ordered, key=lambda fig: _alphanumeric_sort_key(str(fig.get("figure_id") or "")))


def _sort_figure_ids_alphanumerically(figure_ids: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for fid in figure_ids or []:
        key, label = _canonical_figure_reference(str(fid or ""))
        if not key or not label:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(label)
    return sorted(out, key=_alphanumeric_sort_key)


def _build_pmc_figure_url(pmcid: str, figure_id: str) -> str | None:
    number = _primary_figure_number(figure_id)
    if not number:
        return None
    normalized = (pmcid or "").strip().upper()
    if not normalized:
        return None
    if not normalized.startswith("PMC"):
        normalized = f"PMC{normalized}"
    return f"https://pmc.ncbi.nlm.nih.gov/articles/{normalized}/figure/F{number}/"


def _candidate_pmc_figure_urls(pmcid: str, figure_id: str) -> list[str]:
    number = _primary_figure_number(figure_id)
    normalized = (pmcid or "").strip().upper()
    if not number or not normalized:
        return []
    if not normalized.startswith("PMC"):
        normalized = f"PMC{normalized}"
    base = f"https://pmc.ncbi.nlm.nih.gov/articles/{normalized}/figure"
    return [
        f"{base}/Figure{number}/",
        f"{base}/F{number}/",
        f"{base}/Fig{number}/",
    ]


def _figure_proxy_cache_paths(url: str) -> tuple[Path, Path]:
    digest = hashlib.sha256(url.casefold().encode("utf-8")).hexdigest()
    bucket = _FIGURE_PROXY_CACHE_DIR / digest[:2] / digest[2:4]
    return bucket / f"{digest}.meta.json", bucket / f"{digest}.bin"


def _read_cached_figure_proxy_image(url: str) -> tuple[bytes, str] | None:
    meta_path, blob_path = _figure_proxy_cache_paths(url)
    if not meta_path.exists() or not blob_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        expires_at = float(meta.get("expires_at", 0) or 0)
        if expires_at and expires_at < time.time():
            meta_path.unlink(missing_ok=True)
            blob_path.unlink(missing_ok=True)
            return None
        content_type = str(meta.get("content_type") or "application/octet-stream")
        return blob_path.read_bytes(), content_type
    except Exception:
        return None


def _write_cached_figure_proxy_image(url: str, *, content_type: str, body: bytes) -> None:
    meta_path, blob_path = _figure_proxy_cache_paths(url)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    expires_at = time.time() + _FIGURE_PROXY_CACHE_TTL_SEC
    meta = {
        "content_type": content_type,
        "size": len(body),
        "cached_at": int(time.time()),
        "expires_at": int(expires_at),
    }
    fd_blob, tmp_blob = tempfile.mkstemp(prefix="figproxy_", suffix=".bin", dir=str(meta_path.parent))
    fd_meta, tmp_meta = tempfile.mkstemp(prefix="figproxy_", suffix=".json", dir=str(meta_path.parent))
    try:
        with os.fdopen(fd_blob, "wb") as fb:
            fb.write(body)
        with os.fdopen(fd_meta, "w", encoding="utf-8") as fm:
            json.dump(meta, fm)
        os.replace(tmp_blob, blob_path)
        os.replace(tmp_meta, meta_path)
    except Exception:
        try:
            os.unlink(tmp_blob)
        except OSError:
            pass
        try:
            os.unlink(tmp_meta)
        except OSError:
            pass


def _pick_first_valid_url(candidates: list[str]) -> str | None:
    if not candidates:
        return None
    try:
        import httpx
    except Exception:
        return candidates[0]

    timeout = httpx.Timeout(6.0, connect=3.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for url in candidates:
            try:
                response = client.get(url, headers=_PMC_FETCH_HEADERS)
                if response.status_code >= 400:
                    continue
                content_type = (response.headers.get("content-type") or "").lower()
                resolved = str(response.url).lower()
                if any(hint in resolved for hint in _BLOCKED_IMG_HINTS):
                    continue
                if content_type.startswith("image/"):
                    return url
                if "html" in content_type and "/figure/" in resolved:
                    return url
            except Exception:
                continue
    return None


def _resolved_primary_preview_map(pmcid: str, figure_ids: list[str]) -> dict[str, str]:
    """Resolve Figure N -> direct image URL via parser utility fallback."""
    normalized = (pmcid or "").strip().upper()
    if not normalized:
        return {}
    if not normalized.startswith("PMC"):
        normalized = f"PMC{normalized}"
    try:
        from researcher_ai.utils.pubmed import get_figure_urls_from_pmcid
    except Exception:
        return {}
    try:
        urls = get_figure_urls_from_pmcid(normalized)
    except Exception:
        return {}
    if not urls:
        return {}

    ordered_ids: list[str] = []
    seen: set[str] = set()
    for fid in figure_ids:
        num = _primary_figure_number(fid)
        if not num:
            continue
        canonical = f"Figure {int(num)}"
        if canonical in seen:
            continue
        seen.add(canonical)
        ordered_ids.append(canonical)
    return {fid: url for fid, url in zip(ordered_ids, urls)}


def _figure_media_rows(
    figures_payload: Any,
    paper_payload: Any,
    job_id: str,
    *,
    validate_urls: bool = True,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(figures_payload, list):
        return rows
    paper = paper_payload if isinstance(paper_payload, dict) else {}
    pmcid = str(paper.get("pmcid") or "").strip().upper()
    if pmcid and not pmcid.startswith("PMC"):
        pmcid = f"PMC{pmcid}"
    figure_ids = [
        str(fig.get("figure_id") or "")
        for fig in figures_payload
        if isinstance(fig, dict)
    ]
    preview_map = _resolved_primary_preview_map(pmcid, figure_ids)
    proxy_base: str | None = None
    for fig in figures_payload:
        if not isinstance(fig, dict):
            continue
        figure_id = str(fig.get("figure_id") or "Unknown Figure")
        is_supplementary = _is_supplementary_figure_id(figure_id)
        urls = _extract_figure_image_urls(fig)
        canonical = _primary_figure_number(figure_id)
        if canonical:
            mapped = preview_map.get(f"Figure {int(canonical)}")
            if mapped:
                urls = [mapped] + urls
        filtered_urls: list[str] = []
        seen_urls: set[str] = set()
        for candidate in urls:
            c = str(candidate or "").strip()
            if not c:
                continue
            lower = c.lower()
            if any(hint in lower for hint in _BLOCKED_IMG_HINTS):
                continue
            if c in seen_urls:
                continue
            seen_urls.add(c)
            filtered_urls.append(c)
        if validate_urls:
            valid_urls: list[str] = []
            for candidate in filtered_urls:
                picked = _pick_first_valid_url([candidate])
                if picked:
                    valid_urls.append(picked)
            urls = valid_urls
        else:
            urls = filtered_urls
        if not urls and pmcid and figure_id and not is_supplementary:
            candidates = _candidate_pmc_figure_urls(pmcid, figure_id)
            if validate_urls:
                pmc_url = _pick_first_valid_url(candidates)
                if pmc_url:
                    urls.append(pmc_url)
            elif candidates:
                urls.append(candidates[0])
        if not urls:
            rows.append(
                {
                    "figure_id": figure_id,
                    "figure_key": _figure_id_key(figure_id),
                    "title": str(fig.get("title") or ""),
                    "caption": str(fig.get("caption") or ""),
                    "purpose": str(fig.get("purpose") or ""),
                    "entries": [],
                    "deferred_parser": "Supplemental Figure Parser" if is_supplementary else "",
                }
            )
            continue
        entries = []
        if proxy_base is None:
            proxy_base = reverse("figure_image_proxy", kwargs={"job_id": job_id})
        for url in urls:
            entries.append(
                {
                    "url": url,
                    "proxy_url": f"{proxy_base}?url={quote(url, safe='')}",
                    "direct_image": _looks_like_image_url(url),
                }
            )
        rows.append(
            {
                "figure_id": figure_id,
                "figure_key": _figure_id_key(figure_id),
                "title": str(fig.get("title") or ""),
                "caption": str(fig.get("caption") or ""),
                "purpose": str(fig.get("purpose") or ""),
                "entries": entries,
                "deferred_parser": "",
            }
        )
    return sorted(rows, key=lambda row: _alphanumeric_sort_key(str(row.get("figure_id") or "")))


def _inject_figure_ground_truth(figures_payload: Any, cleaned: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply user-supplied ground-truth updates to figure JSON payload."""
    payload: list[dict[str, Any]] = []
    if isinstance(figures_payload, list):
        for item in figures_payload:
            if isinstance(item, dict):
                payload.append(dict(item))

    figure_id = str(cleaned.get("figure_id") or "").strip()
    panel_label = str(cleaned.get("panel_label") or "A").strip() or "A"
    plot_type = str(cleaned.get("plot_type") or "other").strip().lower() or "other"
    plot_category = str(cleaned.get("plot_category") or "").strip().lower() or _default_plot_category_for_type(plot_type)
    title_override = str(cleaned.get("title_override") or "").strip()
    caption_override = str(cleaned.get("caption_override") or "").strip()
    description = str(cleaned.get("description") or "").strip()
    x_axis_label = str(cleaned.get("x_axis_label") or "").strip()
    y_axis_label = str(cleaned.get("y_axis_label") or "").strip()
    x_axis_scale = str(cleaned.get("x_axis_scale") or "").strip().lower()
    y_axis_scale = str(cleaned.get("y_axis_scale") or "").strip().lower()
    mark_uncertain = bool(cleaned.get("mark_uncertain"))

    fig_index = None
    for i, fig in enumerate(payload):
        if str(fig.get("figure_id") or "").strip().lower() == figure_id.lower():
            fig_index = i
            break
    if fig_index is None:
        payload.append(
            {
                "figure_id": figure_id or "Figure 1",
                "title": title_override or figure_id or "Figure 1",
                "caption": caption_override,
                "purpose": "Ground truth injected by user.",
                "subfigures": [],
                "layout": {"n_rows": 1, "n_cols": 1, "panel_labels_style": "uppercase"},
                "in_text_context": [],
                "datasets_used": [],
                "methods_used": [],
            }
        )
        fig_index = len(payload) - 1

    figure = payload[fig_index]
    if title_override:
        figure["title"] = title_override
    if caption_override:
        figure["caption"] = caption_override
    if not figure.get("purpose"):
        figure["purpose"] = "Ground truth injected by user."
    subfigures = figure.get("subfigures")
    if not isinstance(subfigures, list):
        subfigures = []
        figure["subfigures"] = subfigures

    sf_index = None
    for i, sf in enumerate(subfigures):
        if str(sf.get("label") or "").strip().lower() == panel_label.lower():
            sf_index = i
            break
    if sf_index is None:
        subfigures.append(
            {
                "label": panel_label,
                "description": description or f"Ground truth panel {panel_label}",
                "plot_type": plot_type,
                "plot_category": plot_category,
                "layers": [{"plot_type": plot_type, "is_primary": True}],
                "classification_confidence": 1.0,
                "composite_confidence": 100.0,
                "confidence_scores": {
                    "label": 100.0,
                    "description": 100.0,
                    "plot_type": 100.0,
                    "plot_category": 100.0,
                    "x_axis": 100.0,
                    "y_axis": 100.0,
                    "color_variable": 100.0,
                    "error_bars": 100.0,
                    "sample_size": 100.0,
                    "data_source": 100.0,
                    "assays": 100.0,
                    "statistical_test": 100.0,
                    "facet_variable": 100.0,
                },
                "alternative_plot_types": [],
                "evidence_spans": ["ground_truth_injected"],
            }
        )
        sf_index = len(subfigures) - 1

    sub = subfigures[sf_index]
    sub["label"] = panel_label
    if description:
        sub["description"] = description
    elif not sub.get("description"):
        sub["description"] = f"Ground truth panel {panel_label}"
    sub["plot_type"] = plot_type
    sub["plot_category"] = plot_category
    sub["layers"] = [{"plot_type": plot_type, "is_primary": True}]
    composite_conf = 20.0 if mark_uncertain else 100.0
    sub["classification_confidence"] = 0.2 if mark_uncertain else 1.0
    sub["composite_confidence"] = composite_conf
    conf_scores = sub.get("confidence_scores") if isinstance(sub.get("confidence_scores"), dict) else {}
    for field in (
        "label",
        "description",
        "plot_type",
        "plot_category",
        "x_axis",
        "y_axis",
        "color_variable",
        "error_bars",
        "sample_size",
        "data_source",
        "assays",
        "statistical_test",
        "facet_variable",
    ):
        conf_scores[field] = composite_conf
    sub["confidence_scores"] = conf_scores
    evidence = [e for e in (sub.get("evidence_spans") or []) if isinstance(e, str) and e.strip()]
    evidence.append("ground_truth_marked_uncertain" if mark_uncertain else "ground_truth_injected")
    sub["evidence_spans"] = list(dict.fromkeys(evidence))

    if x_axis_label or x_axis_scale:
        x_axis = sub.get("x_axis") if isinstance(sub.get("x_axis"), dict) else {}
        if x_axis_label:
            x_axis["label"] = x_axis_label
        elif "label" not in x_axis:
            x_axis["label"] = "x"
        if x_axis_scale:
            x_axis["scale"] = x_axis_scale
        elif "scale" not in x_axis:
            x_axis["scale"] = "linear"
        sub["x_axis"] = x_axis

    if y_axis_label or y_axis_scale:
        y_axis = sub.get("y_axis") if isinstance(sub.get("y_axis"), dict) else {}
        if y_axis_label:
            y_axis["label"] = y_axis_label
        elif "label" not in y_axis:
            y_axis["label"] = "y"
        if y_axis_scale:
            y_axis["scale"] = y_axis_scale
        elif "scale" not in y_axis:
            y_axis["scale"] = "linear"
        sub["y_axis"] = y_axis

    return payload


def _method_assay_rows(method_payload: Any) -> list[dict[str, Any]]:
    """Build plain-English assay/step rows for the methods workflow correction card."""
    warning_rows = _method_warning_rows(method_payload)
    method = method_payload if isinstance(method_payload, dict) else {}
    assay_graph = method.get("assay_graph") if isinstance(method.get("assay_graph"), dict) else {}
    raw_assays = assay_graph.get("assays") if isinstance(assay_graph.get("assays"), list) else []
    rows: list[dict[str, Any]] = []
    for assay_idx, assay in enumerate(raw_assays):
        if not isinstance(assay, dict):
            continue
        assay_name = str(assay.get("name") or f"Assay {assay_idx + 1}").strip() or f"Assay {assay_idx + 1}"
        assay_steps = assay.get("steps") if isinstance(assay.get("steps"), list) else []
        assay_warning_rows = [w for w in warning_rows if w.get("assay_index") == assay_idx and w.get("step_index") is None]
        step_rows: list[dict[str, Any]] = []
        for step_idx, step in enumerate(assay_steps):
            if not isinstance(step, dict):
                continue
            step_number = step.get("step_number")
            if not isinstance(step_number, int):
                step_number = step_idx + 1
            step_warning_rows = [
                w
                for w in warning_rows
                if w.get("assay_index") == assay_idx and w.get("step_index") == step_idx
            ]
            warning_indices = [
                int(w["warning_index"])
                for w in step_warning_rows
                if isinstance(w.get("warning_index"), int)
            ]
            parameters_dict = _normalize_step_parameters(step.get("parameters"))
            step_rows.append(
                {
                    "assay_index": assay_idx,
                    "step_index": step_idx,
                    "step_number": step_number,
                    "description": str(step.get("description") or "").strip(),
                    "software": str(step.get("software") or "").strip(),
                    "software_version": str(step.get("software_version") or "").strip(),
                    "input_data": str(step.get("input_data") or "").strip(),
                    "output_data": str(step.get("output_data") or "").strip(),
                    "parameters": parameters_dict,
                    "parameters_json": json.dumps(parameters_dict, ensure_ascii=True),
                    "code_reference": str(step.get("code_reference") or "").strip(),
                    "warnings": step_warning_rows,
                    "warning_indices_csv": ",".join(str(i) for i in warning_indices),
                    "missing_field_hints": _method_missing_field_hints(step),
                    "is_inferred_stage": False,
                    "inferred_stage_name": "",
                }
            )
        # For template warnings, infer empty stage skeletons users can fill or remove.
        missing_stages = _inferred_missing_stage_items_for_assay(
            warning_rows,
            assay_index=assay_idx,
            assay_name=assay_name,
        )
        inferred_stage_pairs: list[dict[str, Any]] = []
        for offset, stage_item in enumerate(missing_stages):
            stage_name = stage_item["stage_name"]
            inferred_stage_pairs.append(
                {
                    "stage_name": stage_name,
                    "warning_index": stage_item.get("warning_index"),
                }
            )
            step_rows.append(
                {
                    "assay_index": assay_idx,
                    "step_index": len(assay_steps) + offset,
                    "step_number": len(assay_steps) + offset + 1,
                    "description": "",
                    "software": "",
                    "software_version": "",
                    "input_data": "",
                    "output_data": "",
                    "parameters": "",
                    "parameters_json": "{}",
                    "code_reference": "",
                    "warnings": [
                        {
                            "severity": "warning",
                            "summary": f'Missing template stage "{stage_name}"',
                            "raw": f"template_missing_stages:{stage_name}",
                            "category": "template_missing_stages",
                            "warning_index": stage_item.get("warning_index"),
                        }
                    ],
                    "warning_indices_csv": str(stage_item.get("warning_index"))
                    if isinstance(stage_item.get("warning_index"), int)
                    else "",
                    "missing_field_hints": [
                        "Fill the stage details manually, or remove this suggestion if not needed."
                    ],
                    "is_inferred_stage": True,
                    "inferred_stage_name": stage_name,
                    "inferred_stage_warning_index": stage_item.get("warning_index"),
                }
            )
        rows.append(
            {
                "assay_index": assay_idx,
                "assay_name": assay_name,
                "assay_warnings": assay_warning_rows,
                "steps": step_rows,
                "inferred_stage_pairs": inferred_stage_pairs,
                "has_inferred_stage_suggestions": bool(inferred_stage_pairs),
            }
        )
    return rows


def _method_warning_rows(method_payload: Any) -> list[dict[str, Any]]:
    """Classify method parse warnings and infer assay/step applicability."""
    method = method_payload if isinstance(method_payload, dict) else {}
    assay_graph = method.get("assay_graph") if isinstance(method.get("assay_graph"), dict) else {}
    raw_assays = assay_graph.get("assays") if isinstance(assay_graph.get("assays"), list) else []
    parse_warnings = method.get("parse_warnings") if isinstance(method.get("parse_warnings"), list) else []
    rows: list[dict[str, Any]] = []
    for idx, warning in enumerate(parse_warnings):
        raw = str(warning or "").strip()
        if not raw:
            continue
        assay_name_hint = _warning_assay_name_hint(raw)
        assay_idx = _infer_warning_assay_index(raw, raw_assays)
        if assay_idx is None:
            inferred_assay_idx, inferred_step_idx = _infer_warning_target_by_software(raw, raw_assays)
            assay_idx = inferred_assay_idx
            step_idx = inferred_step_idx
        else:
            step_idx = _infer_warning_step_index(raw, raw_assays, assay_idx)
        rows.append(
            {
                "warning_index": idx,
                "raw": raw,
                "category": _warning_category(raw),
                "severity": _warning_severity(raw),
                "summary": _warning_summary(raw),
                "assay_index": assay_idx,
                "step_index": step_idx,
                "assay_name_hint": assay_name_hint,
            }
        )
    return rows


def _warning_category(raw: str) -> str:
    lower = raw.lower()
    if lower.startswith("assay_stub:"):
        return "assay_stub"
    if lower.startswith("dependency_dropped:"):
        return "dependency_dropped"
    if lower.startswith("inferred_parameters_fallback_mode:"):
        return "inferred_parameters_fallback_mode"
    if lower.startswith("inferred_parameters:"):
        return "inferred_parameters"
    if lower.startswith("template_missing_stages:"):
        return "template_missing_stages"
    if " template=" in lower and " missing=" in lower:
        return "template_missing_stages"
    if lower.startswith("paper_rag_vision_fallback:"):
        return "paper_rag_vision_fallback"
    if lower.startswith("assay_filtered_non_computational:"):
        return "assay_filtered_non_computational"
    return "unknown"


def _warning_severity(raw: str) -> str:
    category = _warning_category(raw)
    if category in {"assay_stub", "dependency_dropped"}:
        return "error"
    if category in {"inferred_parameters", "inferred_parameters_fallback_mode", "template_missing_stages"}:
        return "warning"
    return "info"


def _warning_summary(raw: str) -> str:
    category = _warning_category(raw)
    suffix = raw.split(":", 1)[1].strip() if ":" in raw else raw
    if category == "assay_stub":
        return f"Assay parse failed and a fallback stub was created: {suffix}"
    if category == "dependency_dropped":
        return f"Dependency edge dropped: {suffix}"
    if category == "inferred_parameters":
        return f"Parameters inferred rather than extracted: {suffix}"
    if category == "inferred_parameters_fallback_mode":
        return f"Parameter inference used fallback mode: {suffix}"
    if category == "template_missing_stages":
        detail = _parse_template_warning_kv(raw)
        if detail:
            assay_name = detail.get("assay") or "this assay"
            template_name = detail.get("template") or "template"
            missing_names = ", ".join(detail.get("missing_stages") or [])
            if missing_names:
                return (
                    f'Assay "{assay_name}" is missing expected "{template_name}" stage(s): '
                    f"{missing_names}. Add these stages in the assay step outline."
                )
        return f"Expected template stages are missing: {suffix}"
    if category == "assay_filtered_non_computational":
        return f"Assay filtered as non-computational: {suffix}"
    if category == "paper_rag_vision_fallback":
        return "RAG retrieval fell back to vision extraction."
    return raw


def _parse_template_missing_stages(raw: str) -> list[str]:
    if _warning_category(raw) != "template_missing_stages":
        return []
    detail = _parse_template_warning_kv(raw)
    if detail and detail.get("missing_stages"):
        return list(detail["missing_stages"])
    suffix = raw.split(":", 1)[1] if ":" in raw else ""
    tokens = [t.strip() for t in re.split(r"[;,]", suffix) if t.strip()]
    normalized: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(token)
    return normalized


def _parse_template_warning_kv(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if "missing=" not in text:
        return {}
    assay = _warning_assay_name_hint(text)
    template_match = re.search(r"(?i)\btemplate\s*=\s*([A-Za-z0-9_.-]+)", text)
    missing_match = re.search(r"(?i)\bmissing\s*=\s*([A-Za-z0-9_.,;| -]+)", text)
    template_name = str(template_match.group(1) or "").strip() if template_match else ""
    missing_raw = str(missing_match.group(1) or "").strip() if missing_match else ""
    missing_tokens = [
        token.strip()
        for token in re.split(r"[|,;/]", missing_raw)
        if token and token.strip()
    ]
    seen: set[str] = set()
    missing_stages: list[str] = []
    for token in missing_tokens:
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        missing_stages.append(token)
    return {
        "assay": assay,
        "template": template_name,
        "missing_stages": missing_stages,
    }


def _infer_warning_assay_index(raw: str, raw_assays: list[Any]) -> int | None:
    hint = _warning_assay_name_hint(raw)
    if hint:
        hint_key = hint.casefold()
        for idx, assay in enumerate(raw_assays):
            if not isinstance(assay, dict):
                continue
            name = str(assay.get("name") or "").strip()
            if name and name.casefold() == hint_key:
                return idx
    lower = raw.lower()
    for idx, assay in enumerate(raw_assays):
        if not isinstance(assay, dict):
            continue
        name = str(assay.get("name") or "").strip()
        if name and name.lower() in lower:
            return idx
    return None


def _infer_warning_step_index(raw: str, raw_assays: list[Any], assay_idx: int | None) -> int | None:
    if assay_idx is None or assay_idx < 0 or assay_idx >= len(raw_assays):
        return None
    assay = raw_assays[assay_idx] if isinstance(raw_assays[assay_idx], dict) else {}
    steps = assay.get("steps") if isinstance(assay.get("steps"), list) else []
    match = re.search(r"\bstep\s*(\d+)\b", raw, flags=re.IGNORECASE)
    if match:
        parsed = int(match.group(1))
        if 1 <= parsed <= len(steps):
            return parsed - 1
    lower = raw.lower()
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        software = str(step.get("software") or "").strip()
        if software and software.lower() in lower:
            return idx
    return None


def _infer_warning_target_by_software(raw: str, raw_assays: list[Any]) -> tuple[int | None, int | None]:
    lower = raw.lower()
    for assay_idx, assay in enumerate(raw_assays):
        if not isinstance(assay, dict):
            continue
        steps = assay.get("steps") if isinstance(assay.get("steps"), list) else []
        for step_idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            software = str(step.get("software") or "").strip()
            if software and software.lower() in lower:
                return assay_idx, step_idx
    return None, None


def _inferred_missing_stage_items_for_assay(
    warning_rows: list[dict[str, Any]],
    *,
    assay_index: int,
    assay_name: str,
) -> list[dict[str, Any]]:
    stage_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in warning_rows:
        if row.get("category") != "template_missing_stages":
            continue
        target_assay = row.get("assay_index")
        target_hint = str(row.get("assay_name_hint") or "").strip()
        if target_assay is not None and target_assay != assay_index:
            continue
        if target_assay is None and target_hint:
            if target_hint.casefold() != str(assay_name or "").strip().casefold():
                continue
        for stage in _parse_template_missing_stages(str(row.get("raw") or "")):
            key = stage.casefold()
            if key in seen:
                continue
            seen.add(key)
            stage_items.append(
                {
                    "stage_name": stage,
                    "warning_index": row.get("warning_index"),
                }
            )
    return stage_items


def _warning_assay_name_hint(raw: str) -> str:
    text = str(raw or "")
    patterns = [
        r"(?i)\bassay\s*=\s*'([^']+)'",
        r'(?i)\bassay\s*=\s*"([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return str(match.group(1) or "").strip()
    return ""


def _method_missing_field_hints(step: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    if not str(step.get("software_version") or "").strip():
        hints.append("Version format example: 2.7.11b")
    if not str(step.get("input_data") or "").strip():
        hints.append("Input format example: FASTQ.gz, BAM, matrix TSV")
    if not str(step.get("output_data") or "").strip():
        hints.append("Output format example: sorted BAM, quant.sf, DE table CSV")
    if not str(step.get("code_reference") or "").strip():
        hints.append("Code reference example: nf-core/rnaseq@3.14.0")
    return hints


def _normalize_step_parameters(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        normalized: dict[str, str] = {}
        for key, val in value.items():
            normalized[str(key)] = "" if val is None else str(val)
        return normalized
    return {}


def _inject_method_step_correction(method_payload: Any, cleaned: dict[str, Any]) -> dict[str, Any]:
    """Apply a user correction to one methods assay step and return updated payload."""
    payload = json.loads(json.dumps(method_payload if isinstance(method_payload, dict) else {}))
    assay_graph = payload.get("assay_graph")
    if not isinstance(assay_graph, dict):
        raise ValueError("Method payload is missing assay graph data.")
    assays = assay_graph.get("assays")
    if not isinstance(assays, list):
        raise ValueError("Method payload is missing assay list data.")

    assay_idx = int(cleaned.get("assay_index", -1))
    step_idx = int(cleaned.get("step_index", -1))
    if assay_idx < 0 or assay_idx >= len(assays):
        raise ValueError("Selected assay was not found in the current method payload.")
    assay = assays[assay_idx]
    if not isinstance(assay, dict):
        raise ValueError("Selected assay is malformed.")
    steps = assay.get("steps")
    if not isinstance(steps, list):
        raise ValueError("Selected assay has no editable steps.")
    inferred_stage_name = str(cleaned.get("inferred_stage_name") or "").strip()
    warning_indices = _parse_warning_indices_csv(str(cleaned.get("resolved_warning_indices") or ""))
    inferred_stage_warning_index = cleaned.get("inferred_stage_warning_index")
    if isinstance(inferred_stage_warning_index, int):
        warning_indices.append(inferred_stage_warning_index)
    if step_idx == len(steps) and inferred_stage_name:
        steps.append(
            {
                "step_number": len(steps) + 1,
                "description": inferred_stage_name.replace("_", " ").strip().title(),
                "software": "",
                "software_version": "",
                "input_data": "",
                "output_data": "",
                "parameters": {},
                "code_reference": "",
                "inferred_from_warning": "template_missing_stages",
            }
        )
    elif step_idx < 0 or step_idx >= len(steps):
        raise ValueError("Selected step was not found in the current assay.")
    step = steps[step_idx]
    if not isinstance(step, dict):
        raise ValueError("Selected step is malformed.")

    step["description"] = str(cleaned.get("description") or "").strip()
    step["software"] = str(cleaned.get("software") or "").strip()
    step["software_version"] = str(cleaned.get("software_version") or "").strip()
    step["input_data"] = str(cleaned.get("input_data") or "").strip()
    step["output_data"] = str(cleaned.get("output_data") or "").strip()
    parameters_value = cleaned.get("parameters")
    if parameters_value is None:
        step["parameters"] = {}
    elif isinstance(parameters_value, dict):
        step["parameters"] = _normalize_step_parameters(parameters_value)
    else:
        raise ValueError("Parameters must be a dictionary.")
    step["code_reference"] = str(cleaned.get("code_reference") or "").strip()
    if inferred_stage_name:
        step["template_stage"] = inferred_stage_name
        payload = _clear_template_missing_stage_warning(
            payload,
            stage_name=inferred_stage_name,
            warning_index=inferred_stage_warning_index if isinstance(inferred_stage_warning_index, int) else None,
        )
    payload = _remove_parse_warnings_by_indices(payload, warning_indices)
    return payload


def _remove_method_step(method_payload: Any, *, assay_index: int, step_index: int) -> dict[str, Any]:
    """Remove one method assay step and renumber remaining steps."""
    payload = json.loads(json.dumps(method_payload if isinstance(method_payload, dict) else {}))
    assay_graph = payload.get("assay_graph")
    if not isinstance(assay_graph, dict):
        raise ValueError("Method payload is missing assay graph data.")
    assays = assay_graph.get("assays")
    if not isinstance(assays, list):
        raise ValueError("Method payload is missing assay list data.")
    if assay_index < 0 or assay_index >= len(assays):
        raise ValueError("Selected assay was not found in the current method payload.")
    assay = assays[assay_index]
    if not isinstance(assay, dict):
        raise ValueError("Selected assay is malformed.")
    steps = assay.get("steps")
    if not isinstance(steps, list):
        raise ValueError("Selected assay has no editable steps.")
    if step_index < 0 or step_index >= len(steps):
        raise ValueError("Selected step was not found in the current assay.")
    steps.pop(step_index)
    for idx, step in enumerate(steps):
        if isinstance(step, dict):
            step["step_number"] = idx + 1
    return payload


def _parse_warning_indices_csv(text: str) -> list[int]:
    values: list[int] = []
    for token in (text or "").split(","):
        token = token.strip()
        if not token:
            continue
        if token.isdigit():
            values.append(int(token))
    return values


def _remove_parse_warnings_by_indices(method_payload: Any, indices: list[int]) -> dict[str, Any]:
    payload = method_payload if isinstance(method_payload, dict) else {}
    raw_warnings = list(payload.get("parse_warnings") or [])
    if not raw_warnings:
        payload["parse_warnings"] = []
        return payload
    keep: list[str] = []
    skip = {i for i in indices if isinstance(i, int) and i >= 0}
    for idx, warning in enumerate(raw_warnings):
        if idx in skip:
            continue
        keep.append(str(warning))
    payload["parse_warnings"] = keep
    return payload


def _clear_template_missing_stage_warning(
    method_payload: Any,
    *,
    stage_name: str,
    warning_index: int | None = None,
) -> dict[str, Any]:
    payload = method_payload if isinstance(method_payload, dict) else {}
    raw_warnings = list(payload.get("parse_warnings") or [])
    if not raw_warnings:
        payload["parse_warnings"] = []
        return payload
    normalized_target = str(stage_name or "").strip().casefold()
    if not normalized_target:
        return payload

    def _rewrite_warning(text: str) -> str | None:
        if _warning_category(text) != "template_missing_stages":
            return text
        stages = _parse_template_missing_stages(text)
        remaining = [s for s in stages if s.casefold() != normalized_target]
        if not remaining:
            return None
        return f"template_missing_stages: {', '.join(remaining)}"

    updated: list[str] = []
    for idx, warning in enumerate(raw_warnings):
        text = str(warning or "")
        if warning_index is not None and idx != warning_index:
            updated.append(text)
            continue
        rewritten = _rewrite_warning(text)
        if rewritten is None:
            if warning_index is not None and idx != warning_index:
                updated.append(text)
            continue
        updated.append(rewritten)
    payload["parse_warnings"] = updated
    return payload


def _parse_inferred_stage_pair(token: str) -> tuple[int | None, str]:
    text = str(token or "")
    if "::" not in text:
        return None, ""
    left, right = text.split("::", 1)
    left = left.strip()
    right = right.strip()
    warning_index = int(left) if left.isdigit() else None
    return warning_index, right


def _clear_template_missing_stages_by_pairs(method_payload: Any, pair_tokens: list[str]) -> dict[str, Any]:
    payload = method_payload if isinstance(method_payload, dict) else {}
    for token in pair_tokens:
        warning_index, stage_name = _parse_inferred_stage_pair(token)
        if not stage_name:
            continue
        payload = _clear_template_missing_stage_warning(
            payload,
            stage_name=stage_name,
            warning_index=warning_index,
        )
    return payload


def _typed_component(job: dict[str, Any], step: str, mods: dict[str, Any]) -> Any:
    payload = (job.get("components") or {}).get(step)
    if payload is None:
        return None
    if step == "paper":
        return mods["Paper"].model_validate(payload)
    if step == "figures":
        return [mods["Figure"].model_validate(x) for x in payload]
    if step == "method":
        return mods["Method"].model_validate(payload)
    if step == "datasets":
        return [mods["Dataset"].model_validate(x) for x in payload]
    if step == "software":
        return [mods["Software"].model_validate(x) for x in payload]
    if step == "pipeline":
        return mods["Pipeline"].model_validate(payload)
    return payload


def _persist_component(job_id: str, step: str, payload: Any, source: str) -> None:
    job = get_job(job_id) or {}
    comps = dict(job.get("components") or {})
    meta = dict(job.get("component_meta") or {})
    status, missing = _component_status(step, payload)
    comps[step] = payload
    meta[step] = {
        "status": status,
        "missing": missing,
        "source": source,
    }
    update_job(job_id, components=comps, component_meta=meta)
    if step == "method":
        diagnostics = _extract_method_diagnostics(payload)
        if diagnostics:
            update_job(job_id, job_metadata=diagnostics)


class _RunnerContractError(ValueError):
    """Raised when adapter output cannot satisfy the portal component contract."""


def _runner_mode() -> str:
    mode = str(os.environ.get("RESEARCHER_AI_PORTAL_RUNNER_MODE", "orchestrator") or "orchestrator").strip().lower()
    if mode not in {"legacy", "orchestrator"}:
        return "legacy"
    return mode


def _runner_timeout_seconds(mode: str) -> float:
    if mode == "orchestrator":
        raw = os.environ.get("RESEARCHER_AI_PORTAL_ORCHESTRATOR_HARD_TIMEOUT_SECONDS", "7200")
    else:
        raw = os.environ.get("RESEARCHER_AI_PORTAL_LEGACY_HARD_TIMEOUT_SECONDS", "10800")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 7200.0 if mode == "orchestrator" else 10800.0
    return max(60.0, value)


def _runner_soft_timeout_seconds(mode: str) -> float:
    if mode == "orchestrator":
        raw = os.environ.get("RESEARCHER_AI_PORTAL_ORCHESTRATOR_SOFT_TIMEOUT_SECONDS", "3600")
    else:
        raw = os.environ.get("RESEARCHER_AI_PORTAL_LEGACY_SOFT_TIMEOUT_SECONDS", "5400")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 3600.0 if mode == "orchestrator" else 5400.0
    return max(30.0, value)


def _run_with_timeout(
    fn: Callable[[], Any],
    *,
    timeout_seconds: float,
    label: str,
) -> Any:
    """Run blocking work with a strict wall-clock timeout."""
    timeout = max(1.0, float(timeout_seconds))
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            raise TimeoutError(f"{label} exceeded timeout ({int(timeout)}s)") from exc


def _run_with_timeout_and_heartbeat(
    fn: Callable[[], Any],
    *,
    timeout_seconds: float,
    label: str,
    heartbeat_seconds: float = 15.0,
    on_heartbeat: Callable[[], None] | None = None,
) -> Any:
    """Run blocking work with timeout while emitting periodic heartbeats."""
    timeout = max(1.0, float(timeout_seconds))
    heartbeat = max(1.0, float(heartbeat_seconds))
    started_at = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn)
        while True:
            elapsed = time.monotonic() - started_at
            remaining = timeout - elapsed
            if remaining <= 0:
                future.cancel()
                raise TimeoutError(f"{label} exceeded timeout ({int(timeout)}s)")
            try:
                return future.result(timeout=min(heartbeat, remaining))
            except concurrent.futures.TimeoutError:
                if on_heartbeat is not None:
                    try:
                        on_heartbeat()
                    except Exception:
                        # Heartbeats are best-effort and should not fail a running parse.
                        pass


def _runtime_researcher_ai_version() -> str:
    module_version = "unknown"
    try:
        import researcher_ai  # type: ignore

        module_version = str(getattr(researcher_ai, "__version__", "unknown") or "unknown")
    except Exception:
        module_version = "unknown"
    try:
        from importlib import metadata as importlib_metadata

        dist_version = str(importlib_metadata.version("researcher-ai") or "unknown")
    except Exception:
        dist_version = "unknown"
    if dist_version != "unknown":
        return dist_version
    return module_version


def _report_version_drift(job_id: str, version: str) -> None:
    expected = str(os.environ.get("RESEARCHER_AI_EXPECTED_VERSION", "") or "").strip()
    if not expected:
        _log_job_event(
            job_id,
            "researcher-ai version drift check disabled; set RESEARCHER_AI_EXPECTED_VERSION to enable.",
            level="info",
            step="paper",
        )
        return
    if expected == version:
        return
    _log_job_event(
        job_id,
        f"researcher-ai version drift detected: expected={expected}, runtime={version}",
        level="warning",
    )


def _orchestrator_heartbeat_seconds() -> float:
    raw = str(os.environ.get("RESEARCHER_AI_PORTAL_ORCHESTRATOR_HEARTBEAT_SECONDS", "15") or "15").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 15.0
    return max(1.0, value)


def _orchestrator_step_from_stage(stage: str, *, default: str = "paper") -> str:
    """Map orchestrator stage identifiers to portal step keys."""
    s = (stage or "").strip().lower()
    if not s:
        return default
    if "paper" in s:
        return "paper"
    if "figure" in s:
        return "figures"
    if "dataset" in s:
        return "datasets"
    if "software" in s:
        return "software"
    if "method" in s or "validation" in s:
        return "method"
    if "workflow_graph" in s or "pipeline" in s or "builder" in s or "review" in s or "completed" in s:
        return "pipeline"
    return default


def _compact_orchestrator_metadata(
    value: Any,
    *,
    max_string_len: int = _ORCHESTRATOR_META_MAX_STRING_LEN,
    max_list_len: int = _ORCHESTRATOR_META_MAX_LIST_LEN,
    max_depth: int = _ORCHESTRATOR_META_MAX_DEPTH,
    _depth: int = 0,
) -> Any:
    if _depth >= max_depth:
        return "...truncated"
    if isinstance(value, str):
        if len(value) <= max_string_len:
            return value
        return f"{value[:max_string_len]}...truncated"
    if isinstance(value, list):
        out = [
            _compact_orchestrator_metadata(
                item,
                max_string_len=max_string_len,
                max_list_len=max_list_len,
                max_depth=max_depth,
                _depth=_depth + 1,
            )
            for item in value[:max_list_len]
        ]
        if len(value) > max_list_len:
            out.append("...truncated")
        return out
    if isinstance(value, dict):
        return {
            str(k): _compact_orchestrator_metadata(
                v,
                max_string_len=max_string_len,
                max_list_len=max_list_len,
                max_depth=max_depth,
                _depth=_depth + 1,
            )
            for k, v in value.items()
        }
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump(mode="json")
        except TypeError:
            dumped = value.model_dump()
        return _compact_orchestrator_metadata(
            dumped,
            max_string_len=max_string_len,
            max_list_len=max_list_len,
            max_depth=max_depth,
            _depth=_depth + 1,
        )
    return value


def _extract_orchestrator_diagnostics(state: dict[str, Any]) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    for key in (
        "dataset_parse_errors",
        "workflow_graph_validation_issues",
        "method_validation_report",
        "validation_blocked",
        "build_attempts",
        "max_build_attempts",
        "stage",
        "progress",
    ):
        value = state.get(key)
        if value is not None:
            diagnostics[key] = _compact_orchestrator_metadata(value)
    return diagnostics


def _stuck_job_timeout_seconds() -> float:
    raw = os.environ.get("RESEARCHER_AI_PORTAL_STUCK_JOB_TIMEOUT_SECONDS", str(_STUCK_JOB_TIMEOUT_SECONDS))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = float(_STUCK_JOB_TIMEOUT_SECONDS)
    return max(60.0, value)


def _maybe_fail_stuck_job(job_id: str, *, user: Any, job: dict[str, Any]) -> dict[str, Any]:
    if str(job.get("status") or "") != "in_progress":
        return job
    row = WorkflowJob.objects.filter(id=job_id, user=user).first()
    if row is None or row.updated_at is None:
        return job
    age_seconds = (time.time() - row.updated_at.timestamp())
    threshold = _stuck_job_timeout_seconds()
    if age_seconds <= threshold:
        return job
    reason = (
        f"Run stalled with no job updates for {int(age_seconds)}s (> {int(threshold)}s). "
        "This often happens when a background parser thread is interrupted (for example during server reload). "
        "Please retry the parse."
    )
    update_job(
        job_id,
        user=user,
        status="failed",
        stage="Run stalled — retry required",
        error=reason,
        job_metadata={
            "stalled_run_detected": True,
            "stalled_seconds_without_update": int(age_seconds),
            "stalled_timeout_seconds": int(threshold),
        },
    )
    _log_job_event(job_id, reason, level="warning", step=str(job.get("current_step") or "paper"))
    return get_job(job_id, user=user) or job


def _normalize_orchestrator_components(
    state: dict[str, Any],
    mods: dict[str, Any],
) -> dict[str, Any]:
    """Normalize orchestrator state into portal component payloads.

    Raises _RunnerContractError when a required shape cannot be validated.
    """

    def _dump(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, list):
            out: list[Any] = []
            for item in value:
                out.append(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)
            return out
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        return value

    raw_components: dict[str, Any] = {
        "paper": _dump(state.get("paper")),
        "figures": _dump(state.get("figures")),
        "method": _dump(state.get("method")),
        "datasets": _dump(state.get("datasets")),
        "software": _dump(state.get("software")),
        "pipeline": _dump(state.get("pipeline")),
    }
    components: dict[str, Any] = {}
    for step in STEP_ORDER:
        payload = raw_components.get(step)
        if payload is None:
            continue
        try:
            validated = _validate_component_json(step, payload, mods)
        except Exception as exc:
            raise _RunnerContractError(
                f"contract_validation_failed:{step}: {type(exc).__name__}: {exc}"
            ) from exc
        components[step] = validated

    method_payload = components.get("method")
    if method_payload is not None:
        assay_graph = (method_payload or {}).get("assay_graph") or {}
        if not isinstance(assay_graph.get("assays"), list):
            raise _RunnerContractError("contract_validation_failed:method.assay_graph.assays must be a list")
        if not isinstance(assay_graph.get("dependencies"), list):
            raise _RunnerContractError("contract_validation_failed:method.assay_graph.dependencies must be a list")

    pipeline_payload = components.get("pipeline")
    if pipeline_payload is not None:
        config = (pipeline_payload or {}).get("config") or {}
        if not isinstance(config, dict):
            raise _RunnerContractError("contract_validation_failed:pipeline.config must be an object")
        if not isinstance(config.get("steps"), list):
            raise _RunnerContractError("contract_validation_failed:pipeline.config.steps must be a list")

    return components


def _orchestrator_status_and_metadata(state: dict[str, Any], method_payload: Any) -> tuple[str, dict[str, Any]]:
    metadata: dict[str, Any] = _extract_orchestrator_diagnostics(state)
    if isinstance(method_payload, dict):
        metadata.update(_extract_method_diagnostics(method_payload))

    if bool(state.get("human_review_required", False)):
        metadata["human_review_required"] = True
        summary = state.get("human_review_summary")
        if isinstance(summary, dict):
            metadata["human_review_summary"] = summary
        return "needs_human_review", metadata

    stage = str(state.get("stage") or "").strip().lower()
    if stage == "needs_human_review":
        metadata["human_review_required"] = True
        summary = state.get("human_review_summary")
        if isinstance(summary, dict):
            metadata["human_review_summary"] = summary
        return "needs_human_review", metadata

    if state.get("pipeline") is not None:
        return "completed", metadata
    return "failed", metadata


def _run_orchestrator_job(
    job_id: str,
    *,
    llm_api_key: str,
    llm_model: str,
    hard_timeout_seconds: float | None = None,
) -> None:
    job = get_job(job_id)
    if job is None:
        raise Http404("Unknown job id")
    if llm_model:
        job["llm_model"] = llm_model
    if llm_api_key:
        job["llm_api_key"] = llm_api_key

    update_job(job_id, status="in_progress", current_step="paper", stage="Running WorkflowOrchestrator", progress=0)
    _log_job_event(job_id, "WorkflowOrchestrator run started", step="paper")

    timeout_raw = str(os.environ.get("RESEARCHER_AI_PORTAL_ORCHESTRATOR_CALL_TIMEOUT_SECONDS", "") or "").strip()
    timeout_value: float
    if timeout_raw:
        try:
            timeout_value = float(timeout_raw)
        except ValueError:
            timeout_value = _runner_timeout_seconds("orchestrator")
    elif hard_timeout_seconds is not None:
        timeout_value = float(hard_timeout_seconds)
    else:
        timeout_value = _runner_timeout_seconds("orchestrator")
    timeout_value = max(1.0, timeout_value)

    mods = _import_runtime_modules()
    with _llm_env(job):
        from researcher_ai.models.paper import PaperSource
        from researcher_ai.pipeline.orchestrator import WorkflowOrchestrator

        source_type_key = str(job.get("source_type") or "pmid").strip().lower()
        source_type = PaperSource.PMID
        if source_type_key == "pdf":
            source_type = PaperSource.PDF
        elif source_type_key == "pmcid":
            source_type = PaperSource.PMCID
        elif source_type_key == "doi":
            source_type = PaperSource.DOI
        elif source_type_key == "url":
            source_type = PaperSource.URL

        orchestrator = WorkflowOrchestrator()

        source_value = str(job.get("source") or "")
        heartbeat_seconds = _orchestrator_heartbeat_seconds()
        state: dict[str, Any] = {
            "source": source_value,
            "source_type": source_type,
            "progress": 0,
            "stage": "initialized",
            "build_attempts": 0,
            "max_build_attempts": int(getattr(orchestrator, "max_build_attempts", 2) or 2),
        }

        node_plan: list[tuple[str, str, str]] = [
            ("_node_parse_paper", "paper", STEP_LABELS["paper"]),
            ("_node_parse_figures", "figures", STEP_LABELS["figures"]),
            ("_node_parse_methods", "method", STEP_LABELS["method"]),
            ("_node_parse_datasets", "datasets", STEP_LABELS["datasets"]),
            ("_node_parse_software", "software", STEP_LABELS["software"]),
            ("_node_build_workflow_graph", "pipeline", "Workflow Graph"),
        ]
        if str(getattr(orchestrator, "bioworkflow_mode", "warn")) != "off":
            node_plan.append(("_node_validate_method", "method", "Method Validation"))

        def _run_node(node_name: str, step_key: str, label: str) -> None:
            running_progress = int(state.get("progress") or _progress_for_step(step_key) or 0)
            running_progress = max(0, min(99, running_progress))
            running_stage = f"Running {label}"

            update_job(
                job_id,
                status="in_progress",
                current_step=step_key,
                stage=running_stage,
                progress=running_progress,
            )
            _log_job_event(job_id, running_stage, step=step_key)

            node_fn = getattr(orchestrator, node_name, None)
            if not callable(node_fn):
                raise _RunnerContractError(f"orchestrator_node_missing:{node_name}")

            def _invoke() -> dict[str, Any]:
                result = node_fn(state)
                if not isinstance(result, dict):
                    raise _RunnerContractError(
                        f"orchestrator_node_invalid_result:{node_name}:{type(result).__name__}"
                    )
                return result

            result = _run_with_timeout_and_heartbeat(
                _invoke,
                timeout_seconds=timeout_value,
                label=f"WorkflowOrchestrator.{node_name}",
                heartbeat_seconds=heartbeat_seconds,
                on_heartbeat=lambda: update_job(
                    job_id,
                    status="in_progress",
                    current_step=step_key,
                    stage=running_stage,
                    progress=running_progress,
                ),
            )
            state.update(result)

            stage_raw = str(state.get("stage") or "").strip()
            mapped_step = _orchestrator_step_from_stage(stage_raw, default=step_key)
            progress_value = int(state.get("progress") or running_progress)
            progress_value = max(0, min(99, progress_value))
            stage_text = stage_raw or f"Completed {label}"
            update_job(
                job_id,
                status="in_progress",
                current_step=mapped_step,
                stage=stage_text,
                progress=progress_value,
            )
            _log_job_event(job_id, stage_text, step=mapped_step)

        for node_name, step_key, label in node_plan:
            _run_node(node_name, step_key, label)

        while True:
            _run_node("_node_build_pipeline", "pipeline", STEP_LABELS["pipeline"])
            next_after = getattr(orchestrator, "_next_after_build_pipeline", None)
            if not callable(next_after):
                break
            if str(next_after(state)) == "end":
                break

    components = _normalize_orchestrator_components(state, mods)
    for step in STEP_ORDER:
        if step in components:
            _persist_component(job_id, step, components[step], "parsed_orchestrator")

    status, metadata = _orchestrator_status_and_metadata(state, components.get("method"))
    if status == "needs_human_review":
        update_job(
            job_id,
            status="needs_human_review",
            current_step="pipeline",
            progress=100,
            stage="needs_human_review",
            job_metadata=metadata,
        )
        _log_job_event(job_id, "WorkflowOrchestrator requires human review", step="pipeline", level="warning")
        return
    if status == "completed":
        update_job(
            job_id,
            status="completed",
            current_step="pipeline",
            progress=100,
            stage="All steps complete — ready for review",
            job_metadata=metadata,
        )
        _log_job_event(job_id, "WorkflowOrchestrator completed full parse", step="pipeline")
        return
    raise _RunnerContractError("orchestrator_result_missing_pipeline")


def _run_step(
    job_id: str,
    step: str,
    *,
    llm_api_key: str = "",
    llm_model: str = "",
    force_reparse: bool = False,
) -> None:
    job = get_job(job_id)
    if job is None:
        raise Http404("Unknown job id")
    if llm_model:
        job["llm_model"] = llm_model
    if llm_api_key:
        job["llm_api_key"] = llm_api_key

    mods = _import_runtime_modules()
    with _llm_env(job):
        model = job.get("llm_model")
        if step == "paper":
            _log_job_event(job_id, "Initializing paper parser", step=step)
            source_type_key = str(job.get("source_type") or "pmid").strip().lower()
            source_type = mods["PaperSource"].PMID
            source_value = str(job.get("source") or "")
            if source_type_key == "pdf":
                source_type = mods["PaperSource"].PDF
                pdf_path = Path(source_value).expanduser()
                if not pdf_path.is_absolute():
                    pdf_path = pdf_path.resolve()
                if not pdf_path.exists() or not pdf_path.is_file():
                    raise FileNotFoundError(
                        "Uploaded PDF is no longer available for parsing. "
                        "Please re-upload the PDF and retry."
                    )
                source_value = str(pdf_path)
            elif source_type_key == "pmcid":
                source_type = mods["PaperSource"].PMCID
            elif source_type_key == "doi":
                source_type = mods["PaperSource"].DOI
            elif source_type_key == "url":
                source_type = mods["PaperSource"].URL

            canonical_id = _canonical_paper_cache_id(source_type_key, str(job.get("source") or ""))
            if not force_reparse:
                cached = PaperCache.objects.filter(canonical_id=canonical_id, llm_model=str(model or "")).first()
                if cached is not None:
                    _log_job_event(job_id, "Paper cache hit; loading cached parse", step=step)
                    _persist_component(job_id, "paper", cached.paper_json, "cached")
                    if cached.figures_json:
                        _persist_component(job_id, "figures", cached.figures_json, "cached")
                    update_job(job_id, stage="Loaded paper from cache")
                    _log_job_event(job_id, "Loaded paper from cache", step=step)
                    return
            _log_job_event(job_id, f"Paper cache miss; parsing source ({source_type_key})", step=step)

            parser = mods["PaperParser"](llm_model=model)
            paper = parser.parse(source_value, source_type=source_type)
            paper_json = paper.model_dump(mode="json")
            _persist_component(job_id, "paper", paper_json, "parsed")
            _log_job_event(job_id, "Paper parsing complete", step=step)
            PaperCache.objects.update_or_create(
                canonical_id=canonical_id,
                defaults={
                    "paper_json": paper_json,
                    "llm_model": str(model or ""),
                },
            )
            _log_job_event(job_id, "Paper cache updated", step=step)
            return

        paper = _typed_component(job, "paper", mods)
        if paper is None:
            raise ValueError("Paper step must be completed first.")

        if step == "figures":
            _log_job_event(job_id, "Initializing figure parser", step=step)
            if getattr(paper, "source", None) == mods["PaperSource"].PDF:
                pdf_path = Path(str(getattr(paper, "source_path", "") or "")).expanduser()
                if not pdf_path.is_absolute():
                    pdf_path = pdf_path.resolve()
                if not pdf_path.exists() or not pdf_path.is_file():
                    raise FileNotFoundError(
                        "Staged PDF for figure parsing was not found. "
                        "Please restart the parse with the original PDF."
                    )
            parser = mods["FigureParser"](llm_model=model)
            figure_ids = list(getattr(paper, "figure_ids", []) or [])
            if not figure_ids:
                recover = getattr(parser, "_recover_figure_ids", None)
                if callable(recover):
                    figure_ids = list(recover(paper) or [])
            if not figure_ids:
                figure_ids = _infer_figure_ids_from_paper_text(paper)
            figure_ids = _collapse_figure_reference_variants(figure_ids)
            primary_figure_ids, supplementary_figure_ids = _split_primary_and_supplementary_figure_ids(figure_ids)
            primary_figure_ids = sorted(primary_figure_ids, key=_alphanumeric_sort_key)
            supplementary_figure_ids = sorted(supplementary_figure_ids, key=_alphanumeric_sort_key)
            update_job(job_id, supplementary_figure_ids=supplementary_figure_ids)
            figure_ids = primary_figure_ids
            _log_job_event(
                job_id,
                f"Figure discovery complete: {len(primary_figure_ids)} primary, {len(supplementary_figure_ids)} supplementary",
                step=step,
            )
            if not figure_ids:
                _persist_component(job_id, "figures", [], "parsed")
                update_job(job_id, figure_parse_total=0, figure_parse_current=0, stage="No primary figures to parse")
                _log_job_event(job_id, "No primary figures detected", step=step)
                return

            parsed_figures: list[dict[str, Any]] = []
            total = len(figure_ids)
            update_job(job_id, figure_parse_total=total, figure_parse_current=0)
            prev_pct = _progress_for_step("paper")
            end_pct = _progress_for_step("figures")
            for idx, fig_id in enumerate(figure_ids, start=1):
                update_job(
                    job_id,
                    stage=f"Starting {fig_id} ({idx}/{total})",
                    figure_parse_current=max(0, idx - 1),
                    figure_parse_total=total,
                )
                _log_job_event(job_id, f"Parsing {fig_id} ({idx}/{total})", step=step)
                single = paper.model_copy(update={"figure_ids": [fig_id]})
                chunk = parser.parse_all_figures(single)
                if chunk:
                    first = chunk[0]
                    if isinstance(first, dict):
                        parsed_figures.append(dict(first))
                    else:
                        parsed_figures.append(first.model_dump(mode="json"))
                stage = f"Parsing {fig_id} ({idx}/{total})"
                pct = prev_pct + int(round(((end_pct - prev_pct) * idx) / max(total, 1)))
                update_job(
                    job_id,
                    stage=stage,
                    progress=min(end_pct, pct),
                    figure_parse_current=idx,
                    figure_parse_total=total,
                )
                _persist_component(
                    job_id,
                    "figures",
                    _sort_figures_and_panels_alphanumerically(parsed_figures),
                    "parsed_partial",
                )
                _log_job_event(job_id, f"Parsed {fig_id}; {idx}/{total} complete", step=step)
            figures_json = _sort_figures_and_panels_alphanumerically(parsed_figures)
            _persist_component(job_id, "figures", figures_json, "parsed")
            _log_job_event(job_id, f"Figure parsing complete: {len(figures_json)} figure records", step=step)
            source_type_key = str(job.get("source_type") or "pmid").strip().lower()
            canonical_id = _canonical_paper_cache_id(source_type_key, str(job.get("source") or ""))
            if canonical_id:
                PaperCache.objects.update_or_create(
                    canonical_id=canonical_id,
                    defaults={
                        "paper_json": (get_job(job_id) or {}).get("components", {}).get("paper", {}) or {},
                        "figures_json": figures_json,
                        "llm_model": str(model or ""),
                    },
                )
                _log_job_event(job_id, "Figure cache updated", step=step)
            return

        figures = _typed_component(job, "figures", mods) or []
        if step == "method":
            _log_job_event(job_id, "Running methods parser", step=step)
            rag_mode = str(os.environ.get("RESEARCHER_AI_RAG_MODE", "per_job") or "per_job").strip().lower()
            rag_base = str(os.environ.get("RESEARCHER_AI_RAG_BASE_DIR", "") or "").strip()
            n_sections = len(paper.sections or [])
            n_figs = len(figures or [])
            indexing_started_at = _iso_utc_now()
            indexing_started_monotonic = time.monotonic()
            rag_events: list[dict[str, Any]] = []
            rag_events.append(
                {
                    "ts": indexing_started_at,
                    "phase": "indexing",
                    "level": "info",
                    "message": f"Indexing {n_sections} paper sections + {n_figs} figure captions into RAG store",
                }
            )
            _log_job_event(
                job_id,
                f"Indexing {n_sections} paper sections + {n_figs} figure captions into RAG store",
                step=step,
            )
            update_job(job_id, stage="Methods: Building RAG index")
            indexing_finished_at = _iso_utc_now()
            indexing_duration_s = round(max(0.0, time.monotonic() - indexing_started_monotonic), 3)
            generation_started_at = _iso_utc_now()
            generation_started_monotonic = time.monotonic()
            rag_events.append(
                {
                    "ts": generation_started_at,
                    "phase": "generation",
                    "level": "info",
                    "message": f"Sending methods extraction request to {model}",
                }
            )
            if rag_mode == "shared":
                if rag_base:
                    shared_dir = Path(rag_base).expanduser().resolve()
                else:
                    shared_dir = (DJANGO_ROOT / ".rag_chroma").resolve()
                shared_dir.mkdir(parents=True, exist_ok=True)
                parser = mods["MethodsParser"](llm_model=model, rag_persist_dir=str(shared_dir))
                update_job(job_id, stage="Methods: Sending LLM request (may take 3–10 min)")
                _log_job_event(job_id, f"Sending methods extraction request to {model}…", step=step)
                method = parser.parse(paper, figures=figures, computational_only=True)
            else:
                temp_parent = Path(rag_base).expanduser().resolve() if rag_base else None
                if temp_parent is not None:
                    temp_parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(
                    prefix=f"researcher_ai_rag_{job_id}_",
                    dir=str(temp_parent) if temp_parent is not None else None,
                ) as rag_tmp_dir:
                    parser = mods["MethodsParser"](llm_model=model, rag_persist_dir=rag_tmp_dir)
                    update_job(job_id, stage="Methods: Sending LLM request (may take 3–10 min)")
                    _log_job_event(job_id, f"Sending methods extraction request to {model}…", step=step)
                    method = parser.parse(paper, figures=figures, computational_only=True)
            generation_finished_at = _iso_utc_now()
            generation_duration_s = round(max(0.0, time.monotonic() - generation_started_monotonic), 3)
            n_assays = len((method.assay_graph.assays if hasattr(method, "assay_graph") and method.assay_graph else []) or [])
            _log_job_event(job_id, f"LLM response received — {n_assays} assay(s) extracted", step=step)
            update_job(job_id, stage="Methods: Persisting results")
            method_payload = method.model_dump(mode="json")
            _persist_component(job_id, "method", method_payload, "parsed")
            parse_warnings = list(method_payload.get("parse_warnings") or [])
            retrieval = _extract_retrieval_metrics(parse_warnings, (get_job(job_id) or {}).get("parse_logs") or [])
            diagnostics = _extract_method_diagnostics(method_payload)
            rag_events.append(
                {
                    "ts": generation_finished_at,
                    "phase": "post_parse_validation",
                    "level": "info",
                    "message": f"LLM response received — {n_assays} assay(s) extracted",
                }
            )
            for warning in parse_warnings:
                rag_events.append(
                    {
                        "ts": _iso_utc_now(),
                        "phase": "post_parse_validation",
                        "level": "warning",
                        "message": str(warning),
                    }
                )
            rag_events.sort(key=lambda row: str(row.get("ts") or ""))
            update_job(
                job_id,
                job_metadata={
                    "rag_workflow": {
                        "mode": rag_mode,
                        "indexing": {
                            "section_count": n_sections,
                            "figure_caption_count": n_figs,
                            "started_at": indexing_started_at,
                            "finished_at": indexing_finished_at,
                            "duration_s": indexing_duration_s,
                        },
                        "retrieval": retrieval,
                        "generation": {
                            "model": str(model or ""),
                            "started_at": generation_started_at,
                            "finished_at": generation_finished_at,
                            "duration_s": generation_duration_s,
                        },
                        "result": {
                            "assay_count": n_assays,
                            "parse_warning_count": len(parse_warnings),
                            "review_required": bool(diagnostics.get("human_review_required", False)),
                        },
                        "events": rag_events,
                    }
                },
            )
            _log_job_event(job_id, "Methods parsing complete", step=step)
            return

        method = _typed_component(job, "method", mods)
        if step == "datasets":
            _log_job_event(job_id, "Running dataset parsers", step=step)
            geo = mods["GEOParser"]()
            sra = mods["SRAParser"]()
            section_text = "\n".join((getattr(sec, "text", "") or "") for sec in (paper.sections or []))
            supp_chunks: list[str] = []
            for item in (paper.supplementary_items or []):
                supp_chunks.append(
                    " ".join(
                        p
                        for p in [
                            (getattr(item, "item_id", "") or "").strip(),
                            (getattr(item, "label", "") or "").strip(),
                            (getattr(item, "description", "") or "").strip(),
                        ]
                        if p
                    )
                )
            supp_text = "\n".join(supp_chunks)
            combined = "\n".join(
                [
                    paper.raw_text or "",
                    section_text,
                    supp_text,
                    (method.data_availability if method else "") or "",
                    (method.code_availability if method else "") or "",
                    "\n".join((getattr(f, "caption", "") or "") for f in figures),
                ]
            )
            accessions = _collect_accessions(combined)
            capped = accessions[:25]
            _log_job_event(job_id, f"Found {len(accessions)} accession candidates; resolving up to {len(capped)}", step=step)
            datasets = []
            for idx, acc in enumerate(capped, 1):
                update_job(job_id, stage=f"Datasets: Resolving {acc} ({idx}/{len(capped)})")
                _log_job_event(job_id, f"Resolving {acc} ({idx}/{len(capped)})", step=step)
                try:
                    if acc.startswith(("GSE", "GSM", "GDS", "GPL")):
                        ds = geo.parse(acc)
                    elif acc.startswith(("SRP", "SRX", "SRR", "ERP", "ERR", "PRJNA", "PRJEB")):
                        ds = sra.parse(acc)
                    else:
                        ds = None
                    if ds is not None:
                        datasets.append(ds.model_dump(mode="json"))
                        _log_job_event(job_id, f"Resolved {acc} → {getattr(ds, 'title', acc)[:60]}", step=step)
                    else:
                        _log_job_event(job_id, f"Skipped {acc} (unrecognised prefix)", step=step)
                except Exception as ds_exc:
                    _log_job_event(job_id, f"Failed to resolve {acc}: {ds_exc}", level="warning", step=step)
            _persist_component(job_id, "datasets", datasets, "parsed")
            _log_job_event(job_id, f"Dataset parsing complete: {len(datasets)} datasets resolved", step=step)
            return

        datasets = _typed_component(job, "datasets", mods) or []
        if step == "software":
            _log_job_event(job_id, "Running software parser", step=step)
            update_job(job_id, stage=f"Software: Sending LLM request to {model}…")
            _log_job_event(job_id, f"Extracting software tools via LLM ({model})", step=step)
            parser = mods["SoftwareParser"](llm_model=model)
            software = parser.parse_from_method(method) if method else []
            _persist_component(job_id, "software", [s.model_dump(mode="json") for s in software], "parsed")
            _log_job_event(job_id, f"Software parsing complete: {len(software)} tool(s) identified", step=step)
            return

        software = _typed_component(job, "software", mods) or []
        if step == "pipeline":
            method_payload = method.model_dump(mode="json") if method is not None else {}
            method_diagnostics = _extract_method_diagnostics(method_payload)
            if method_diagnostics.get("human_review_required", False):
                update_job(
                    job_id,
                    status="needs_human_review",
                    current_step=step,
                    progress=100,
                    stage="needs_human_review",
                    job_metadata=method_diagnostics,
                )
                _log_job_event(job_id, "Pipeline build blocked: human review required.", step=step, level="warning")
                return
            _log_job_event(job_id, "Building pipeline from parsed components", step=step)
            update_job(job_id, stage=f"Pipeline: Sending LLM request to {model}…")
            _log_job_event(
                job_id,
                f"Assembling execution graph from {len(software)} tool(s), "
                f"{len(datasets)} dataset(s) via LLM",
                step=step,
            )
            builder = mods["PipelineBuilder"](llm_model=model)
            pipeline = builder.build(method, datasets, software, figures)
            n_steps = len((pipeline.config.steps if hasattr(pipeline, "config") and pipeline.config else []) or [])
            _log_job_event(job_id, f"LLM response received — {n_steps} pipeline step(s)", step=step)
            _persist_component(job_id, "pipeline", pipeline.model_dump(mode="json"), "parsed")
            _log_job_event(job_id, "Pipeline build complete", step=step)
            return

    raise ValueError(f"Unknown step: {step}")


def _progress_for_step(step: str) -> int:
    idx = STEP_ORDER.index(step)
    return int(round((idx / (len(STEP_ORDER) - 1)) * 100))


def _run_all_steps_async(
    job_id: str,
    *,
    llm_api_key: str,
    llm_model: str,
    force_reparse: bool = False,
) -> None:
    """Run all parsing steps in sequence.

    Designed to execute in a daemon thread started by ``start_parse``.
    Progress, stage, and log events are written to the job store so the
    ``/jobs/<id>/status/`` endpoint can stream them to the progress page.
    """
    mode = _runner_mode()
    version = _runtime_researcher_ai_version()
    _report_version_drift(job_id, version)
    _log_job_event(job_id, f"Runner selected: {mode} (researcher-ai {version})", step="paper")

    started_at = time.monotonic()
    soft_timeout = _runner_soft_timeout_seconds(mode)
    hard_timeout = _runner_timeout_seconds(mode)
    _log_job_event(
        job_id,
        f"Runner limits: soft={int(soft_timeout)}s hard={int(hard_timeout)}s",
        step="paper",
    )
    try:
        if mode == "orchestrator" and force_reparse:
            # Orchestrator is full-run authoritative and does not support cache bypass semantics.
            _log_job_event(
                job_id,
                "force_reparse requested; orchestrator mode currently ignores per-step cache bypass.",
                level="warning",
                step="paper",
            )

        if mode == "orchestrator":
            _run_orchestrator_job(
                job_id,
                llm_api_key=llm_api_key,
                llm_model=llm_model,
                hard_timeout_seconds=hard_timeout,
            )
        else:
            for step in STEP_ORDER:
                # Bail out if the job was explicitly failed or deleted externally
                job = get_job(job_id)
                if job is None:
                    return
                if str(job.get("status") or "") in {"failed", "needs_human_review"}:
                    return
                _dispatch_workflow_step(
                    job_id,
                    step,
                    llm_api_key=llm_api_key,
                    llm_model=llm_model,
                    force_reparse=force_reparse,
                )
            # All six steps finished successfully
            final_job = get_job(job_id) or {}
            if str(final_job.get("status") or "") != "needs_human_review":
                update_job(
                    job_id,
                    status="completed",
                    progress=100,
                    stage="All steps complete — ready for review",
                )
        elapsed = time.monotonic() - started_at
        if elapsed > soft_timeout:
            _log_job_event(
                job_id,
                f"Runner exceeded soft timeout ({int(elapsed)}s > {int(soft_timeout)}s)",
                level="warning",
                step="pipeline",
            )
        if elapsed > hard_timeout:
            raise TimeoutError(f"runner hard timeout exceeded ({int(elapsed)}s > {int(hard_timeout)}s)")
        if str((get_job(job_id) or {}).get("status") or "") == "needs_human_review":
            _log_job_event(job_id, "Pipeline paused for required human review.", step="pipeline", level="warning")
        else:
            _log_job_event(job_id, "Full pipeline parse complete. Redirecting to dashboard.", step="pipeline")
    except TimeoutError as exc:
        user_error = _humanize_step_error(exc)
        update_job(job_id, status="failed", stage="Pipeline timed out", error=user_error)
        _log_job_event(job_id, f"Pipeline run timed out: {user_error}", level="error", step="pipeline")
    except Exception as exc:
        # _dispatch_workflow_step already marks the job as failed and logs the
        # step-level error; log an extra top-level note for clarity.
        update_job(job_id, status="failed", stage="Pipeline failed", error=_humanize_step_error(exc))
        _log_job_event(
            job_id,
            f"Pipeline run aborted: {exc}",
            level="error",
            step="",
        )


def _build_step_chips_enhanced(
    job: dict[str, Any],
    step_confidence_scores: dict[str, float | None] | None = None,
    step_action_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Return per-step stepper chips with derived presentation state.

    Each chip has::

        {
            "id":               str,    # step key
            "label":            str,    # human label
            "meta":             dict,   # component_meta entry
            "stepper_state":    str,    # "running" | "confirmed" | "needs-review" | "not-started"
            "action_count":     int,    # actionable items targeting this step
            "confidence_score": float | None,  # None if not applicable
        }
    """
    meta_map = job.get("component_meta") or {}
    current_step = job.get("current_step") or ""
    job_status = job.get("status") or ""
    scores = step_confidence_scores or {}
    counts = step_action_counts or {}
    chips = []
    for s in STEP_ORDER:
        meta = meta_map.get(s) or {"status": "missing", "source": "none", "missing": []}
        status = meta.get("status") or "missing"
        conf = scores.get(s)
        action_count = counts.get(s, 0)

        if job_status == "in_progress" and current_step == s:
            stepper_state = "running"
        elif status == "missing":
            stepper_state = "not-started"
        elif conf is not None and conf < 70.0:
            stepper_state = "needs-review"
        elif status in ("found", "inferred") and conf is None:
            # Non-method steps: found/inferred → confirmed; action items → needs-review
            stepper_state = "needs-review" if action_count > 0 else "confirmed"
        elif conf is not None and conf >= 70.0:
            stepper_state = "confirmed"
        else:
            stepper_state = "not-started"

        chips.append(
            {
                "id": s,
                "label": STEP_LABELS[s],
                "meta": meta,
                "stepper_state": stepper_state,
                "action_count": action_count,
                "confidence_score": conf,
            }
        )
    return chips


def _humanize_step_error(exc: Exception) -> str:
    text = str(exc or "").strip()
    lower = text.lower()
    if "429" in lower or "rate limit" in lower or "too many requests" in lower:
        return "Vision model rate limit reached. Please try again in 1 minute."
    return text or exc.__class__.__name__


def _job_result_from_components(job: dict[str, Any]) -> dict[str, Any]:
    comps = job.get("components") or {}
    return {
        "paper": comps.get("paper") or {},
        "figures": comps.get("figures") or [],
        "method": comps.get("method") or {},
        "datasets": comps.get("datasets") or [],
        "software": comps.get("software") or [],
        "pipeline": comps.get("pipeline") or {},
    }


def _dashboard_context(job: dict[str, Any]) -> dict[str, Any]:
    result = _job_result_from_components(job)
    paper = result["paper"]
    figures = _sort_figures_and_panels_alphanumerically(result["figures"] or [])
    result["figures"] = figures
    method = result["method"]
    # Normalise dataset dicts: older parsed jobs store the repository type as
    # "source"; templates that pre-date this fix also reference "source_type".
    # Add both keys so the template works with either name.
    raw_datasets = result["datasets"] or []
    datasets: list[dict] = []
    for ds in raw_datasets:
        if not isinstance(ds, dict):
            continue
        d = dict(ds)
        src = d.get("source") or d.get("source_type") or ""
        d.setdefault("source", src)
        d.setdefault("source_type", src)
        datasets.append(d)
    result["datasets"] = datasets
    software = result["software"]
    # Normalise pipeline dict: ensure pipeline.config.steps always exists so
    # the template can iterate it safely without variable-ref default filters.
    raw_pipeline = result["pipeline"]
    if not isinstance(raw_pipeline, dict):
        raw_pipeline = {}
    raw_config = raw_pipeline.get("config")
    if not isinstance(raw_config, dict):
        raw_pipeline = dict(raw_pipeline)
        raw_pipeline["config"] = {"steps": []}
    elif not isinstance(raw_config.get("steps"), list):
        raw_pipeline = dict(raw_pipeline)
        raw_pipeline["config"] = dict(raw_config)
        raw_pipeline["config"]["steps"] = []
    pipeline = raw_pipeline
    result["pipeline"] = pipeline
    meta = job.get("component_meta") or {}

    status_counts = {"found": 0, "inferred": 0, "missing": 0}
    for step in STEP_ORDER:
        state = (meta.get(step) or {}).get("status", "missing")
        status_counts[state] = status_counts.get(state, 0) + 1

    summary = {
        "title": paper.get("title", "Untitled"),
        "paper_type": paper.get("paper_type", "unknown"),
        "figure_count": len(figures),
        "assay_count": len((method.get("assay_graph") or {}).get("assays") or []),
        "dataset_count": len(datasets),
        "software_count": len(software),
        "pipeline_step_count": len((pipeline.get("config") or {}).get("steps") or []),
        "status_counts": status_counts,
        "component_meta": meta,
    }
    confidence = compute_confidence(result)
    summary["overall_confidence"] = confidence.get("overall", 50.0)

    actionable_items = compute_actionable_items(result, confidence)
    job_metadata = dict(job.get("job_metadata") or {})

    # Per-step confidence summary keyed by step name — used by the stepper
    # to colour-code circles on both workflow_step.html and dashboard.html.
    # We map each parsing step to the mean assay confidence that depends on it.
    assay_confidences = confidence.get("assay_confidences") or {}
    step_confidence_scores: dict[str, float | None] = {}
    for s in STEP_ORDER:
        if s == "method" and assay_confidences:
            step_confidence_scores[s] = round(
                sum(v.get("overall", 50.0) for v in assay_confidences.values())
                / len(assay_confidences),
                1,
            )
        else:
            step_confidence_scores[s] = None  # use component status for non-method steps

    # Count actionable items per step-target for stepper badges
    step_action_counts: dict[str, int] = {s: 0 for s in STEP_ORDER}
    tab_to_step = {
        "editing": "method",
        "datasets": "datasets",
        "figures": "figures",
        "advanced": "pipeline",
        "workflow-graph": "method",
    }
    for item in actionable_items:
        mapped = tab_to_step.get(item["fix_target_tab"])
        if mapped:
            step_action_counts[mapped] = step_action_counts.get(mapped, 0) + 1

    return {
        "paper": paper,
        "figures": figures,
        "method": method,
        "datasets": datasets,
        "software": software,
        "pipeline": pipeline,
        "confidence": confidence,
        "actionable_items": actionable_items,
        "job_metadata": job_metadata,
        "review_required": bool(job_metadata.get("human_review_required", False)),
        "review_summary": job_metadata.get("human_review_summary"),
        "vision_fallback_count": job_metadata.get("vision_fallback_count"),
        "vision_fallback_latency_seconds": job_metadata.get("vision_fallback_latency_seconds"),
        "step_confidence_scores": step_confidence_scores,
        "step_action_counts": step_action_counts,
        "summary": summary,
        "paper_json": json.dumps(paper, indent=2),
        "figures_json": json.dumps(figures, indent=2),
        "method_json": json.dumps(method, indent=2),
        "datasets_json": json.dumps(datasets, indent=2),
        "software_json": json.dumps(software, indent=2),
        "pipeline_json": json.dumps(pipeline, indent=2),
    }


def _dispatch_workflow_step(
    job_id: str,
    step: str,
    *,
    llm_api_key: str,
    llm_model: str,
    force_reparse: bool = False,
) -> None:
    _log_job_event(job_id, f"Queued step: {STEP_LABELS.get(step, step)}", step=step)
    label = STEP_LABELS.get(step, step)
    update_job(job_id, status="in_progress", current_step=step, stage=f"Running {label}")
    _log_job_event(job_id, f"Worker started step: {label}", step=step)
    try:
        _run_step(
            job_id,
            step,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            force_reparse=force_reparse,
        )
        refreshed = get_job(job_id) or {}
        if str(refreshed.get("status") or "") == "needs_human_review":
            update_job(job_id, current_step=step, progress=100, stage="needs_human_review")
            _log_job_event(job_id, "Step ended with human review required.", step=step, level="warning")
            return
        progress = _progress_for_step(step)
        update_job(job_id, status="in_progress", current_step=step, progress=progress, stage=f"Completed {label}")
        _log_job_event(job_id, f"Step completed: {label}", step=step)
    except Exception as exc:
        user_error = _humanize_step_error(exc)
        update_job(job_id, status="failed", current_step=step, stage=f"{label} failed", error=user_error)
        _log_job_event(job_id, f"Step failed: {label}: {user_error}", level="error", step=step)
        raise


def _dispatch_rebuild(job_id: str, edited_step: str, *, llm_api_key: str, llm_model: str) -> None:
    dirty_steps = invalidated_steps(get_job(job_id) or {}, edited_step)
    for step in dirty_steps:
        _dispatch_workflow_step(
            job_id,
            step,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            force_reparse=False,
        )
    if dirty_steps:
        refreshed = get_job(job_id) or {}
        if str(refreshed.get("status") or "") != "needs_human_review":
            update_job(job_id, status="in_progress", current_step=dirty_steps[-1])


def _infer_last_edited_step(job: dict[str, Any]) -> str:
    meta = job.get("component_meta") or {}
    last = "method"
    for step in STEP_ORDER:
        source = str((meta.get(step) or {}).get("source") or "")
        if source.startswith("corrected"):
            last = step
    return last


@login_required
@require_POST
def delete_job(request, job_id: str):
    """Delete a workflow job owned by the current user and redirect home."""
    try:
        job = WorkflowJob.objects.get(id=job_id, user=request.user)
        job.delete()
    except WorkflowJob.DoesNotExist:
        pass  # already gone or not owned by user — silently ignore
    return redirect("home")


@login_required
@require_GET
def home(request):
    jobs = WorkflowJob.objects.filter(user=request.user).order_by("-created_at")[:25]
    return render(
        request,
        "researcher_ai_portal/home.html",
        {
            "user": request.user,
            "llm_model": "gpt-5.4",
            "llm_api_key": os.environ.get("LLM_API_KEY", ""),
            "force_reparse": False,
            "common_llm_models": COMMON_LLM_MODELS,
            "recent_jobs": jobs,
        },
    )


@login_required
@require_POST
def start_parse(request):
    pmid = (request.POST.get("pmid") or "").strip()
    llm_model_raw = request.POST.get("llm_model") or "gpt-5.4"
    llm_api_key_raw = request.POST.get("llm_api_key") or os.environ.get("LLM_API_KEY", "")
    force_reparse = str(request.POST.get("force_reparse") or "").strip().lower() in {"1", "true", "on", "yes"}
    pdf_file = request.FILES.get("paper_pdf")

    if not pmid and not pdf_file:
        return render(
            request,
            "researcher_ai_portal/home.html",
            {
                "user": request.user,
                "error": "Provide a PubMed ID or upload a PDF.",
                "pmid": pmid,
                "llm_model": llm_model_raw,
                "llm_api_key": llm_api_key_raw,
                "force_reparse": force_reparse,
                "common_llm_models": COMMON_LLM_MODELS,
            },
            status=400,
        )

    try:
        llm_model = _validate_llm_model(llm_model_raw)
        llm_api_key = _validate_llm_api_key(llm_api_key_raw, _infer_provider(llm_model))
    except ValueError as exc:
        return render(
            request,
            "researcher_ai_portal/home.html",
            {
                "user": request.user,
                "error": str(exc),
                "pmid": pmid,
                "llm_model": llm_model_raw,
                "llm_api_key": llm_api_key_raw,
                "force_reparse": force_reparse,
                "common_llm_models": COMMON_LLM_MODELS,
            },
            status=400,
        )

    if pdf_file is not None:
        source = str(_stage_uploaded_pdf(pdf_file))
        source_type = "pdf"
        input_value = pdf_file.name
    else:
        source = pmid
        source_type = "pmid"
        input_value = pmid

    request.session[_SESSION_LLM_API_KEY_FIELD] = _encrypt_session_secret(llm_api_key)
    request.session.pop("llm_api_key", None)
    request.session["llm_model"] = llm_model
    request.session.set_expiry(7200)

    job_id = create_job(
        source_type,
        input_value,
        user=request.user,
        source=source,
        source_type=source_type,
        llm_model=llm_model,
        status="in_progress",
        stage="Ready for Paper Parser",
        progress=0,
        current_step="paper",
    )
    _log_job_event(job_id, f"Parse job created for {source_type}: {input_value}", step="paper")
    _log_job_event(job_id, f"Starting full pipeline parse with {llm_model}", step="paper")

    # Launch all six parsing steps in a background daemon thread so the HTTP
    # response returns immediately and the user sees live progress on the
    # parse_progress page.
    thread = threading.Thread(
        target=_run_all_steps_async,
        kwargs={
            "job_id": job_id,
            "llm_api_key": llm_api_key,
            "llm_model": llm_model,
            "force_reparse": force_reparse,
        },
        daemon=True,
        name=f"parse-{job_id[:8]}",
    )
    thread.start()

    # Redirect immediately to the live progress page.
    return redirect("parse_progress", job_id=job_id)


@login_required
@require_GET
def job_progress(request, job_id: str):
    job = get_job(job_id, user=request.user)
    if job is None:
        raise Http404("Unknown job id")
    step = job.get("current_step", "paper")
    return redirect("workflow_step", job_id=job_id, step=step)


@login_required
@require_GET
def parse_progress(request, job_id: str):
    """Render the parsing-progress / completion page for a job.

    On load the page immediately polls the job status endpoint.  If parsing is
    already done (synchronous flow) it shows the completion panel with a
    colour-coded stepper and "Review pipeline →" CTA.  If still running it
    animates the stage indicators until done.
    """
    job = get_job(job_id, user=request.user)
    if job is None:
        raise Http404("Unknown job id")
    job = _maybe_fail_stuck_job(job_id, user=request.user, job=job)

    # Build the stage list for the stepper indicators
    stages = [{"id": s, "label": STEP_LABELS[s]} for s in STEP_ORDER]

    return render(
        request,
        "researcher_ai_portal/progress.html",
        {
            "job_id": job_id,
            "stages": stages,
            "completion_steps": stages,
            # JSON blobs consumed by progress.html JS
            "step_order_json": json.dumps(STEP_ORDER),
            "step_labels_json": json.dumps(STEP_LABELS),
        },
    )


@login_required
@require_GET
def rag_workflow(request, job_id: str):
    job = get_job(job_id, user=request.user)
    if job is None:
        raise Http404("Unknown job id")
    context = _dashboard_context(job)
    rag_payload = build_rag_workflow_payload(job)
    context.update(
        {
            "job_id": job_id,
            "rag_workflow": rag_payload,
            "rag_workflow_json": rag_payload,
        }
    )
    return render(request, "researcher_ai_portal/rag_workflow.html", context)


@login_required
@require_GET
def job_status(request, job_id: str):
    job = get_job(job_id, user=request.user)
    if job is None:
        return JsonResponse({"error": "unknown job"}, status=404)
    job = _maybe_fail_stuck_job(job_id, user=request.user, job=job)
    job_metadata = dict(job.get("job_metadata") or {})
    payload = {
        "job_id": job["job_id"],
        "status": job.get("status", "in_progress"),
        "progress": job.get("progress", 0),
        "stage": job.get("stage", ""),
        "error": job.get("error", ""),
        "current_step": job.get("current_step", "paper"),
        "figure_parse_current": job.get("figure_parse_current", 0),
        "figure_parse_total": job.get("figure_parse_total", 0),
        # Phase 5: include component_meta for the progress page completion stepper
        "component_meta": job.get("component_meta") or {},
        "review_required": bool(job_metadata.get("human_review_required", False)) or None,
        "review_summary": job_metadata.get("human_review_summary"),
        "vision_fallback_count": job_metadata.get("vision_fallback_count"),
        "vision_fallback_latency_seconds": job_metadata.get("vision_fallback_latency_seconds"),
    }
    payload = merge_logs(payload, job_id)
    return JsonResponse(payload)


@login_required
@require_GET
def figure_image_proxy(request, job_id: str):
    import httpx

    job = get_job(job_id, user=request.user)
    if job is None:
        raise Http404("Unknown job id")

    url = _normalize_url(request.GET.get("url", ""))
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return HttpResponse("Invalid image URL.", status=400, content_type="text/plain")
    cached_image = _read_cached_figure_proxy_image(url)
    if cached_image is not None:
        body, content_type = cached_image
        return HttpResponse(body, content_type=content_type)

    timeout = httpx.Timeout(20.0, connect=8.0)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url, headers=_PMC_FETCH_HEADERS)
            response.raise_for_status()
            content_type = (response.headers.get("content-type") or "").lower()
            resolved_url = str(response.url).lower()
            if any(hint in resolved_url for hint in _BLOCKED_IMG_HINTS):
                return HttpResponse("Blocked placeholder image detected.", status=502, content_type="text/plain")
            if content_type.startswith("image/"):
                if "svg" in content_type and any(hint in response.text.lower() for hint in _BLOCKED_IMG_HINTS):
                    return HttpResponse("Blocked placeholder image detected.", status=502, content_type="text/plain")
                _write_cached_figure_proxy_image(url, content_type=content_type, body=response.content)
                return HttpResponse(response.content, content_type=content_type)
            if "html" in content_type:
                html = response.text
                html_candidates: list[str] = []
                for match in _HTML_IMG_RE.finditer(html):
                    html_candidates.append(urljoin(str(response.url), _normalize_url(match.group(1))))
                # Also scan inline script/data URLs for blob-hosted images.
                for extracted in _urls_from_text(html):
                    html_candidates.append(urljoin(str(response.url), extracted))

                seen: set[str] = set()
                for img_url in html_candidates:
                    key = img_url.casefold()
                    if key in seen:
                        continue
                    seen.add(key)
                    lower = img_url.lower()
                    if any(hint in lower for hint in _BLOCKED_IMG_HINTS):
                        continue
                    if not _looks_like_image_url(img_url):
                        continue
                    img_resp = client.get(img_url, headers=_PMC_FETCH_HEADERS)
                    img_resp.raise_for_status()
                    img_type = (img_resp.headers.get("content-type") or "").lower()
                    if not img_type.startswith("image/"):
                        continue
                    if "svg" in img_type and any(hint in img_resp.text.lower() for hint in _BLOCKED_IMG_HINTS):
                        continue
                    _write_cached_figure_proxy_image(url, content_type=img_type, body=img_resp.content)
                    if img_url != url:
                        _write_cached_figure_proxy_image(img_url, content_type=img_type, body=img_resp.content)
                    return HttpResponse(img_resp.content, content_type=img_type)
    except Exception as exc:  # pragma: no cover - network-dependent
        return HttpResponse(f"Failed to fetch image: {exc}", status=502, content_type="text/plain")

    return HttpResponse("URL did not resolve to an image.", status=415, content_type="text/plain")


@login_required
def workflow_step(request, job_id: str, step: str):
    job = get_job(job_id, user=request.user)
    if job is None:
        raise Http404("Unknown job id")
    if step not in STEP_ORDER:
        raise Http404("Unknown workflow step")

    error = ""
    info = ""

    form_prefix = f"step_{step}"
    gt_form_prefix = f"gt_{step}"
    method_step_form_prefix = f"method_step_{step}"
    components_now = job.get("components") or {}
    method_now = components_now.get("method") if isinstance(components_now.get("method"), dict) else {}
    figures_now = _sort_figures_and_panels_alphanumerically(components_now.get("figures") or [])
    figure_ids_for_gt = [
        str(f.get("figure_id"))
        for f in figures_now
        if isinstance(f, dict) and str(f.get("figure_id") or "").strip()
    ]
    if not figure_ids_for_gt:
        paper_comp = components_now.get("paper") or {}
        figure_ids_for_gt = [str(x) for x in (paper_comp.get("figure_ids") or []) if str(x).strip()]
    figure_ids_for_gt = _sort_figure_ids_alphanumerically(figure_ids_for_gt)
    figure_gt_form: FigureGroundTruthForm | None = None
    method_step_correction_form: MethodStepCorrectionForm | None = None

    if request.method == "POST":
        action = request.POST.get("action", "") or request.POST.get("step_action", "")
        try:
            if action == "run":
                llm_api_key = _session_llm_api_key(request)
                llm_model = request.session.get("llm_model", job.get("llm_model", "gpt-5.4"))
                update_job(job_id, user=request.user, stage=f"Running {STEP_LABELS[step]}", current_step=step)
                _dispatch_workflow_step(
                    job_id,
                    step,
                    llm_api_key=llm_api_key,
                    llm_model=llm_model,
                )
                info = f"{STEP_LABELS[step]} started."
            elif action == "save":
                form = ComponentJSONForm(request.POST, prefix=form_prefix)
                if not form.is_valid():
                    raise ValueError(f"Invalid JSON: {form.errors.get('component_json', ['Unknown error'])[0]}")
                payload = form.cleaned_data.get("component_json")
                mods = _import_runtime_modules()
                validated = _validate_component_json(step, payload, mods)
                _persist_component(job_id, step, validated, "corrected_by_user")
                info = f"Saved manual corrections for {STEP_LABELS[step]}."
            elif action == "inject_ground_truth" and step == "figures":
                figure_gt_form = FigureGroundTruthForm(
                    request.POST,
                    prefix=gt_form_prefix,
                    figure_ids=figure_ids_for_gt,
                )
                if not figure_gt_form.is_valid():
                    raise ValueError("Ground-truth form is invalid. Please check figure/panel fields.")
                payload = _inject_figure_ground_truth(figures_now, figure_gt_form.cleaned_data)
                mods = _import_runtime_modules()
                validated = _validate_component_json("figures", payload, mods)
                _persist_component(job_id, "figures", validated, "ground_truth_injected")
                info = "Injected figure ground-truth values."
            elif action == "inject_method_step_correction" and step == "method":
                method_step_correction_form = MethodStepCorrectionForm(request.POST, prefix=method_step_form_prefix)
                if not method_step_correction_form.is_valid():
                    raise ValueError("Method step correction form is invalid. Please check the step fields.")
                payload = _inject_method_step_correction(method_now, method_step_correction_form.cleaned_data)
                mods = _import_runtime_modules()
                validated = _validate_component_json("method", payload, mods)
                _persist_component(job_id, "method", validated, "corrected_by_user")
                info = "Saved method step correction."
            elif action == "remove_method_step" and step == "method":
                assay_idx = int(request.POST.get("assay_index", "-1"))
                step_idx = int(request.POST.get("step_index", "-1"))
                warning_indices = _parse_warning_indices_csv(str(request.POST.get("warning_indices", "")))
                payload = _remove_method_step(method_now, assay_index=assay_idx, step_index=step_idx)
                payload = _remove_parse_warnings_by_indices(payload, warning_indices)
                mods = _import_runtime_modules()
                validated = _validate_component_json("method", payload, mods)
                _persist_component(job_id, "method", validated, "corrected_by_user")
                info = "Removed method step."
            elif action == "remove_inferred_stage_suggestion" and step == "method":
                stage_name = str(request.POST.get("inferred_stage_name", "")).strip()
                warning_index_raw = str(request.POST.get("inferred_stage_warning_index", "")).strip()
                warning_index = int(warning_index_raw) if warning_index_raw.isdigit() else None
                payload = _clear_template_missing_stage_warning(
                    method_now,
                    stage_name=stage_name,
                    warning_index=warning_index,
                )
                mods = _import_runtime_modules()
                validated = _validate_component_json("method", payload, mods)
                _persist_component(job_id, "method", validated, "corrected_by_user")
                info = "Removed inferred stage suggestion."
            elif action == "remove_inferred_stage_suggestions_batch" and step == "method":
                pair_tokens = request.POST.getlist("inferred_stage_pairs")
                payload = _clear_template_missing_stages_by_pairs(method_now, pair_tokens)
                mods = _import_runtime_modules()
                validated = _validate_component_json("method", payload, mods)
                _persist_component(job_id, "method", validated, "corrected_by_user")
                info = "Removed inferred stage suggestions for this assay."
            elif action == "next":
                i = STEP_ORDER.index(step)
                target = STEP_ORDER[min(i + 1, len(STEP_ORDER) - 1)]
                update_job(job_id, user=request.user, current_step=target)
                return redirect("workflow_step", job_id=job_id, step=target)
            elif action == "prev":
                i = STEP_ORDER.index(step)
                target = STEP_ORDER[max(i - 1, 0)]
                update_job(job_id, user=request.user, current_step=target)
                return redirect("workflow_step", job_id=job_id, step=target)
            elif action == "finish":
                update_job(
                    job_id,
                    user=request.user,
                    status="completed",
                    current_step="pipeline",
                    progress=100,
                    stage="Workflow ready for dashboard",
                )
                return redirect("dashboard", job_id=job_id)
        except Exception as exc:  # pragma: no cover - user-driven
            error = str(exc)

    job = get_job(job_id, user=request.user) or job
    job_metadata = dict(job.get("job_metadata") or {})
    component = (job.get("components") or {}).get(step)
    component_meta = (job.get("component_meta") or {}).get(step, {"status": "missing", "missing": [], "source": "none"})
    figures_for_ui = _sort_figures_and_panels_alphanumerically((job.get("components") or {}).get("figures") or [])
    paper_for_ui = (job.get("components") or {}).get("paper") or {}
    figure_uncertain_rows: list[dict[str, Any]] = []
    figure_provenance_rows: list[dict[str, Any]] = []
    figure_media_rows: list[dict[str, Any]] = []
    merged_figure_rows: list[dict[str, Any]] = []
    figure_parse_current = int(job.get("figure_parse_current", 0) or 0)
    figure_parse_total = int(job.get("figure_parse_total", len(figures_for_ui) if step == "figures" else 0) or 0)
    if figure_parse_total < figure_parse_current:
        figure_parse_total = figure_parse_current
    figure_parse_percent = int(round((figure_parse_current / max(figure_parse_total, 1)) * 100)) if figure_parse_total else 0
    figure_ids_for_ui: list[str] = []
    if step == "figures":
        figure_uncertain_rows = _figure_uncertainty_rows(figures_for_ui)
        figure_provenance_rows = _figure_provenance_rows(figures_for_ui)
        figure_media_rows = _figure_media_rows(figures_for_ui, paper_for_ui, job_id, validate_urls=False)
        merged_figure_rows = _figure_merged_rows(figure_media_rows, figure_uncertain_rows, figure_provenance_rows)
        figure_ids_for_ui = [
            str(f.get("figure_id"))
            for f in figures_for_ui
            if isinstance(f, dict) and str(f.get("figure_id") or "").strip()
        ]
        if not figure_ids_for_ui:
            paper_comp = (job.get("components") or {}).get("paper") or {}
            figure_ids_for_ui = [str(x) for x in (paper_comp.get("figure_ids") or []) if str(x).strip()]
        figure_ids_for_ui = _sort_figure_ids_alphanumerically(figure_ids_for_ui)
    supplementary_figure_ids = _sort_figure_ids_alphanumerically(list(job.get("supplementary_figure_ids") or []))
    method_assay_rows: list[dict[str, Any]] = _method_assay_rows((job.get("components") or {}).get("method") or {})
    i = STEP_ORDER.index(step)
    prev_step = STEP_ORDER[i - 1] if i > 0 else None
    next_step = STEP_ORDER[i + 1] if i < len(STEP_ORDER) - 1 else None
    step_progress = int(round(((i + 1) / len(STEP_ORDER)) * 100))
    progress = int(job.get("progress", step_progress) or step_progress)
    if progress < 0:
        progress = 0
    if progress > 100:
        progress = 100
    update_job(job_id, user=request.user, current_step=step)

    initial_component = component if component is not None else ({} if step in ("paper", "method", "pipeline") else [])
    form = ComponentJSONForm(initial={"component_json": initial_component}, prefix=form_prefix)
    if step == "figures" and figure_gt_form is None:
        figure_gt_form = FigureGroundTruthForm(
            prefix=gt_form_prefix,
            figure_ids=figure_ids_for_ui,
            initial={"figure_id": figure_ids_for_ui[0] if figure_ids_for_ui else "Figure 1"},
        )
    if step == "method" and method_step_correction_form is None:
        method_step_correction_form = MethodStepCorrectionForm(
            prefix=method_step_form_prefix,
            initial={
                "assay_index": 0,
                "step_index": 0,
                "inferred_stage_name": "",
                "resolved_warning_indices": "",
                "inferred_stage_warning_index": None,
            },
        )

    # Phase 5: compute confidence to colour-code stepper circles.
    # Done after the job reload so we reflect the latest component state.
    _step_conf_result = compute_confidence(_job_result_from_components(job))
    _step_assay_confidences = _step_conf_result.get("assay_confidences") or {}
    _step_confidence_scores: dict[str, float | None] = {}
    for _s in STEP_ORDER:
        if _s == "method" and _step_assay_confidences:
            _step_confidence_scores[_s] = round(
                sum(v.get("overall", 50.0) for v in _step_assay_confidences.values())
                / len(_step_assay_confidences),
                1,
            )
        else:
            _step_confidence_scores[_s] = None
    _step_action_items = compute_actionable_items(_job_result_from_components(job), _step_conf_result)
    _step_action_counts: dict[str, int] = {s: 0 for s in STEP_ORDER}
    _tab_to_step = {
        "editing": "method",
        "datasets": "datasets",
        "figures": "figures",
        "advanced": "pipeline",
    }
    for _item in _step_action_items:
        _mapped = _tab_to_step.get(_item["fix_target_tab"])
        if _mapped:
            _step_action_counts[_mapped] = _step_action_counts.get(_mapped, 0) + 1

    stepper_chips = _build_step_chips_enhanced(job, _step_confidence_scores, _step_action_counts)

    return render(
        request,
        "researcher_ai_portal/workflow_step.html",
        {
            "job": job,
            "job_id": job_id,
            "step": step,
            "step_label": STEP_LABELS[step],
            "step_order": STEP_ORDER,
            "step_labels": STEP_LABELS,
            "step_chips": stepper_chips,
            "stepper_chips": stepper_chips,
            "stepper_current_step": step,
            "component_meta": component_meta,
            "form": form,
            "figure_gt_form": figure_gt_form,
            "figure_uncertain_rows": figure_uncertain_rows,
            "figure_provenance_rows": figure_provenance_rows,
            "figure_media_rows": figure_media_rows,
            "merged_figure_rows": merged_figure_rows,
            "figure_parse_current": figure_parse_current,
            "figure_parse_total": figure_parse_total,
            "figure_parse_percent": figure_parse_percent,
            "supplementary_figure_ids": supplementary_figure_ids,
            "method_assay_rows": method_assay_rows,
            "method_step_correction_form": method_step_correction_form,
            "prev_step": prev_step,
            "next_step": next_step,
            "progress": progress,
            "error": error,
            "info": info,
            "status_url": reverse("job_status", kwargs={"job_id": job_id}),
            "review_required": bool(job_metadata.get("human_review_required", False)),
            "review_summary": job_metadata.get("human_review_summary"),
            "vision_fallback_count": job_metadata.get("vision_fallback_count"),
            "vision_fallback_latency_seconds": job_metadata.get("vision_fallback_latency_seconds"),
        },
    )


@login_required
def dashboard(request, job_id: str):
    job = get_job(job_id, user=request.user)
    if job is None:
        raise Http404("Unknown job id")

    figures_now = _sort_figures_and_panels_alphanumerically(((job.get("components") or {}).get("figures") or []))
    figure_ids_for_gt = [
        str(f.get("figure_id"))
        for f in figures_now
        if isinstance(f, dict) and str(f.get("figure_id") or "").strip()
    ]
    if not figure_ids_for_gt:
        paper_comp = (job.get("components") or {}).get("paper") or {}
        figure_ids_for_gt = [str(x) for x in (paper_comp.get("figure_ids") or []) if str(x).strip()]
    figure_ids_for_gt = _sort_figure_ids_alphanumerically(figure_ids_for_gt)

    if request.method == "POST":
        action = request.POST.get("action", "save_component")
        if action == "inject_ground_truth":
            try:
                gt_form = FigureGroundTruthForm(
                    request.POST,
                    prefix="gt_dashboard",
                    figure_ids=figure_ids_for_gt,
                )
                if not gt_form.is_valid():
                    raise ValueError("Ground-truth form is invalid.")
                payload = _inject_figure_ground_truth(figures_now, gt_form.cleaned_data)
                mods = _import_runtime_modules()
                validated = _validate_component_json("figures", payload, mods)
                _persist_component(job_id, "figures", validated, "ground_truth_injected")
            except Exception as exc:
                update_job(job_id, user=request.user, error=f"Dashboard ground-truth injection failed: {exc}")
            return redirect("dashboard", job_id=job_id)
        # NOTE: save_structured_step (legacy Django form POST) was removed in
        # Phase 2 when the Step Editing tab was migrated to PATCH autosave via
        # PATCH /api/v1/jobs/{job_id}/components/method.  All structured-step
        # mutations now go through the FastAPI endpoint, which re-validates via
        # _validate_component_json and recomputes confidence in a single round
        # trip.  No Django form POST is needed or accepted for this action.
        if action == "rebuild_pipeline":
            try:
                edited_step = str(request.POST.get("edited_step") or _infer_last_edited_step(job)).strip()
                if edited_step not in STEP_ORDER:
                    edited_step = "method"
                _dispatch_rebuild(
                    job_id,
                    edited_step,
                    llm_api_key=_session_llm_api_key(request),
                    llm_model=request.session.get("llm_model", ""),
                )
                dirty = invalidated_steps(job, edited_step)
                scope = len(dirty) if dirty else 1
                update_job(
                    job_id,
                    user=request.user,
                    status="in_progress",
                    current_step=dirty[0] if dirty else edited_step,
                    stage=f"Rebuild started from {edited_step} ({scope} step{'s' if scope != 1 else ''})",
                )
            except Exception as exc:
                update_job(job_id, user=request.user, error=f"Rebuild dispatch failed: {exc}")
            return redirect("dashboard", job_id=job_id)

        step = request.POST.get("step", "")
        if step in STEP_ORDER:
            try:
                form_prefix = f"step_{step}"
                form = ComponentJSONForm(request.POST, prefix=form_prefix)
                if not form.is_valid():
                    raise ValueError(f"Invalid JSON: {form.errors.get('component_json', ['Unknown error'])[0]}")
                payload = form.cleaned_data.get("component_json")
                mods = _import_runtime_modules()
                validated = _validate_component_json(step, payload, mods)
                _persist_component(job_id, step, validated, "corrected_in_dashboard")
            except Exception as exc:
                update_job(job_id, user=request.user, error=f"Dashboard correction failed for {step}: {exc}")
        return redirect("dashboard", job_id=job_id)

    context = _dashboard_context(job)
    confidence = context.get("confidence") or {}
    dashboard_name = build_dashboard_app(job_id, context["summary"], context)
    dag_app_name = build_dag_app(
        job_id,
        context.get("method") or {},
        context.get("figures") or [],
        context.get("datasets") or [],
        context.get("pipeline") or {},
        confidence=confidence,
    )
    step_rows = []
    meta = context["summary"].get("component_meta", {})
    components = {
        "paper": context["paper"],
        "figures": context["figures"],
        "method": context["method"],
        "datasets": context["datasets"],
        "software": context["software"],
        "pipeline": context["pipeline"],
    }
    for s in STEP_ORDER:
        step_rows.append(
            {
                "id": s,
                "label": STEP_LABELS[s],
                "meta": meta.get(s, {"status": "missing", "source": "none", "missing": []}),
                "form": ComponentJSONForm(
                    initial={"component_json": components[s]},
                    prefix=f"step_{s}",
                ),
            }
        )
    raw_structured_assays = ((components.get("method") or {}).get("assay_graph") or {}).get("assays") or []
    structured_assays: list[dict[str, Any]] = []
    for assay in raw_structured_assays:
        if not isinstance(assay, dict):
            continue
        row = dict(assay)
        steps = []
        for step in (assay.get("steps") or []):
            if not isinstance(step, dict):
                continue
            step_row = dict(step)
            params = step.get("parameters") or {}
            if not isinstance(params, dict):
                params = {}
            step_row["parameters_json"] = json.dumps(params, ensure_ascii=False, sort_keys=True)
            steps.append(step_row)
        row["steps"] = steps
        structured_assays.append(row)
    dashboard_form_media = ComponentJSONForm().media
    figure_uncertain_rows = _figure_uncertainty_rows(components["figures"])
    figure_provenance_rows = _figure_provenance_rows(components["figures"])
    figure_media_rows = _figure_media_rows(components["figures"], components["paper"], job_id, validate_urls=False)
    merged_figure_rows = _figure_merged_rows(figure_media_rows, figure_uncertain_rows, figure_provenance_rows)
    figure_gt_form = FigureGroundTruthForm(
        prefix="gt_dashboard",
        figure_ids=figure_ids_for_gt,
        initial={"figure_id": figure_ids_for_gt[0] if figure_ids_for_gt else "Figure 1"},
    )
    context.update(
        {
            "job_id": job_id,
            "dashboard_name": dashboard_name,
            "dag_app_name": dag_app_name,
            "job": job,
            "step_order": STEP_ORDER,
            "step_labels": STEP_LABELS,
            "step_rows": step_rows,
            "dashboard_form_media": dashboard_form_media,
            "structured_assays": structured_assays,
            "figure_uncertain_rows": figure_uncertain_rows,
            "figure_provenance_rows": figure_provenance_rows,
            "figure_media_rows": figure_media_rows,
            "merged_figure_rows": merged_figure_rows,
            "figure_gt_form": figure_gt_form,
            "edited_step": _infer_last_edited_step(job),
            "rebuild_steps": invalidated_steps(job, _infer_last_edited_step(job)),
            # Step metadata for the React Flow pipeline builder tab.
            # Passed as a raw Python list so Django's json_script filter
            # serialises it exactly once (pre-encoding via json.dumps would
            # cause double-encoding, leaving STEP_META as a string in JS and
            # crashing STEP_META.find() with a TypeError).
            "step_rows_json": [
                {"id": r["id"], "label": r["label"], "meta": r["meta"]}
                for r in step_rows
            ],
            # Tool data for the redesigned Pipeline Builder tab.
            # Each entry is a minimal projection of the Software model sufficient
            # for the node card and edit panel; passed as raw Python so
            # json_script encodes it exactly once.
            "software_tools_json": [
                {
                    "name": (sw.get("name") or ""),
                    "version": sw.get("version") or "",
                    "description": sw.get("description") or "",
                    "language": sw.get("language") or "",
                    "source_url": sw.get("source_url") or "",
                    "commands": sw.get("commands") or [],
                    "environment": sw.get("environment") or {},
                }
                for sw in (context.get("software") or [])
                if isinstance(sw, dict)
            ],
            # Pipeline steps for default edge wiring and per-tool metadata.
            "pipeline_steps_json": (
                ((context.get("pipeline") or {}).get("config") or {}).get("steps") or []
            ),
            # Method assay graph data for the Pipeline Builder assay selector.
            "method_assays_json": [
                assay
                for assay in raw_structured_assays
                if isinstance(assay, dict)
            ],
            # ── Phase 0: Confidence Command Center ─────────────────────
            # Passed via json_script so the React layer can read them later
            # without double-encoding. The plain Python objects are also
            # available in the template for the server-rendered first paint.
            "actionable_items": context.get("actionable_items") or [],
            "confidence_json": context.get("confidence") or {},
            "step_confidence_scores": context.get("step_confidence_scores") or {},
            "step_action_counts": context.get("step_action_counts") or {},
            # ── Phase 5: Journey stepper ────────────────────────────────
            "stepper_chips": _build_step_chips_enhanced(
                job,
                context.get("step_confidence_scores"),
                context.get("step_action_counts"),
            ),
            "stepper_current_step": "",  # Dashboard has no "current" step
        }
    )
    return render(request, "researcher_ai_portal/dashboard.html", context)
