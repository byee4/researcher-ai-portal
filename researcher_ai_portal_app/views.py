from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import base64
import hashlib
from html import unescape
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from typing import Any
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
from .forms import ComponentJSONForm, FigureGroundTruthForm
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
_LLM_ENV_LOCK = threading.RLock()
_SESSION_LLM_API_KEY_FIELD = "llm_api_key_enc"


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


def _validate_component_json(step: str, payload: Any, mods: dict[str, Any]) -> Any:
    if step == "paper":
        return mods["Paper"].model_validate(payload).model_dump(mode="json")
    if step == "figures":
        return [mods["Figure"].model_validate(x).model_dump(mode="json") for x in (payload or [])]
    if step == "method":
        return mods["Method"].model_validate(payload).model_dump(mode="json")
    if step == "datasets":
        return [mods["Dataset"].model_validate(x).model_dump(mode="json") for x in (payload or [])]
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
    return rows


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
    return rows


def _figure_merged_rows(
    media_rows: list[dict[str, Any]],
    uncertainty_rows: list[dict[str, Any]],
    provenance_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge the three figure data sources into one unified list, sorted
    so uncertain / low-confidence figures appear first.

    Each row has:
        figure_id, figure_key, title, caption, entries, deferred_parser,
        is_uncertain, min_confidence, panels[]
    Each panel has:
        label, plot_type, confidence, is_uncertain, issue_tags,
        confidence_scores, calibration_rules, ground_truth_tags
    """
    # Build lookup dicts keyed by figure_id
    uncertainty_map: dict[str, list[str]] = {
        r["figure_id"]: r["reasons"] for r in uncertainty_rows
    }
    provenance_map: dict[str, list[dict[str, Any]]] = {
        r["figure_id"]: r["panels"] for r in provenance_rows
    }

    merged: list[dict[str, Any]] = []
    for media in media_rows:
        fid = media["figure_id"]
        reasons = uncertainty_map.get(fid, [])
        raw_panels = provenance_map.get(fid, [])

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
            "is_uncertain": is_uncertain,
            "uncertainty_reasons": reasons,
            "panels": panels,
            "min_confidence": min_conf,
        })

    # Sort: uncertain figures first, then by min_confidence ascending
    merged.sort(key=lambda r: (not r["is_uncertain"], r["min_confidence"]))
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
    match = re.search(r"(?i)\b(?:fig(?:ure)?\.?)\s*(\d+)", figure_id or "")
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
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _alphanumeric_sort_key(value: str) -> tuple[tuple[int, Any], ...]:
    text = str(value or "").strip().lower()
    text = re.sub(r"(?i)\bfig(?:ure)?\.?\b", "figure", text)
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


def _figure_media_rows(figures_payload: Any, paper_payload: Any, job_id: str) -> list[dict[str, Any]]:
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
        valid_urls: list[str] = []
        for candidate in urls:
            picked = _pick_first_valid_url([candidate])
            if picked:
                valid_urls.append(picked)
        urls = valid_urls
        if not urls and pmcid and figure_id and not is_supplementary:
            pmc_url = _pick_first_valid_url(_candidate_pmc_figure_urls(pmcid, figure_id))
            if pmc_url:
                urls.append(pmc_url)
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
    return rows


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
            if rag_mode == "shared":
                if rag_base:
                    shared_dir = Path(rag_base).expanduser().resolve()
                else:
                    shared_dir = (DJANGO_ROOT / ".rag_chroma").resolve()
                shared_dir.mkdir(parents=True, exist_ok=True)
                parser = mods["MethodsParser"](llm_model=model, rag_persist_dir=str(shared_dir))
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
                    method = parser.parse(paper, figures=figures, computational_only=True)
            _persist_component(job_id, "method", method.model_dump(mode="json"), "parsed")
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
            _log_job_event(job_id, f"Found {len(accessions)} accession candidates", step=step)
            datasets = []
            for acc in accessions[:25]:
                if acc.startswith(("GSE", "GSM", "GDS", "GPL")):
                    ds = geo.parse(acc)
                elif acc.startswith(("SRP", "SRX", "SRR", "ERP", "ERR", "PRJNA", "PRJEB")):
                    ds = sra.parse(acc)
                else:
                    ds = None
                if ds is not None:
                    datasets.append(ds.model_dump(mode="json"))
            _persist_component(job_id, "datasets", datasets, "parsed")
            _log_job_event(job_id, f"Dataset parsing complete: {len(datasets)} datasets", step=step)
            return

        datasets = _typed_component(job, "datasets", mods) or []
        if step == "software":
            _log_job_event(job_id, "Running software parser", step=step)
            parser = mods["SoftwareParser"](llm_model=model)
            software = parser.parse_from_method(method) if method else []
            _persist_component(job_id, "software", [s.model_dump(mode="json") for s in software], "parsed")
            _log_job_event(job_id, f"Software parsing complete: {len(software)} entries", step=step)
            return

        software = _typed_component(job, "software", mods) or []
        if step == "pipeline":
            _log_job_event(job_id, "Building pipeline from parsed components", step=step)
            builder = mods["PipelineBuilder"](llm_model=model)
            pipeline = builder.build(method, datasets, software, figures)
            _persist_component(job_id, "pipeline", pipeline.model_dump(mode="json"), "parsed")
            _log_job_event(job_id, "Pipeline build complete", step=step)
            return

    raise ValueError(f"Unknown step: {step}")


def _progress_for_step(step: str) -> int:
    idx = STEP_ORDER.index(step)
    return int(round((idx / (len(STEP_ORDER) - 1)) * 100))


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
    figures = result["figures"]
    method = result["method"]
    datasets = result["datasets"]
    software = result["software"]
    pipeline = result["pipeline"]
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
    try:
        update_job(job_id, user=request.user, stage=f"Running {STEP_LABELS['paper']}", current_step="paper")
        _log_job_event(job_id, "Starting Paper Parser", step="paper")
        _dispatch_workflow_step(
            job_id,
            "paper",
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            force_reparse=force_reparse,
        )
    except Exception as exc:  # pragma: no cover - user/network-driven
        update_job(job_id, user=request.user, status="failed", error=str(exc), stage=f"{STEP_LABELS['paper']} failed")
        _log_job_event(job_id, f"Paper Parser failed: {exc}", step="paper", level="error")
    return redirect("workflow_step", job_id=job_id, step="paper")


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
def job_status(request, job_id: str):
    job = get_job(job_id, user=request.user)
    if job is None:
        return JsonResponse({"error": "unknown job"}, status=404)
    payload = {
        "job_id": job["job_id"],
        "status": job.get("status", "in_progress"),
        "progress": job.get("progress", 0),
        "stage": job.get("stage", ""),
        "error": job.get("error", ""),
        "current_step": job.get("current_step", "paper"),
        "figure_parse_current": job.get("figure_parse_current", 0),
        "figure_parse_total": job.get("figure_parse_total", 0),
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
    components_now = job.get("components") or {}
    figures_now = components_now.get("figures") or []
    figure_ids_for_gt = [
        str(f.get("figure_id"))
        for f in figures_now
        if isinstance(f, dict) and str(f.get("figure_id") or "").strip()
    ]
    if not figure_ids_for_gt:
        paper_comp = components_now.get("paper") or {}
        figure_ids_for_gt = [str(x) for x in (paper_comp.get("figure_ids") or []) if str(x).strip()]
    figure_gt_form: FigureGroundTruthForm | None = None

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
    component = (job.get("components") or {}).get(step)
    component_meta = (job.get("component_meta") or {}).get(step, {"status": "missing", "missing": [], "source": "none"})
    figures_for_ui = (job.get("components") or {}).get("figures") or []
    paper_for_ui = (job.get("components") or {}).get("paper") or {}
    figure_uncertain_rows = _figure_uncertainty_rows(figures_for_ui)
    figure_provenance_rows = _figure_provenance_rows(figures_for_ui)
    figure_media_rows = _figure_media_rows(figures_for_ui, paper_for_ui, job_id)
    merged_figure_rows = _figure_merged_rows(figure_media_rows, figure_uncertain_rows, figure_provenance_rows)
    figure_parse_current = int(job.get("figure_parse_current", 0) or 0)
    figure_parse_total = int(job.get("figure_parse_total", len(figures_for_ui) if step == "figures" else 0) or 0)
    if figure_parse_total < figure_parse_current:
        figure_parse_total = figure_parse_current
    figure_parse_percent = int(round((figure_parse_current / max(figure_parse_total, 1)) * 100)) if figure_parse_total else 0
    figure_ids_for_ui = [
        str(f.get("figure_id"))
        for f in figures_for_ui
        if isinstance(f, dict) and str(f.get("figure_id") or "").strip()
    ]
    if not figure_ids_for_ui:
        paper_comp = (job.get("components") or {}).get("paper") or {}
        figure_ids_for_ui = [str(x) for x in (paper_comp.get("figure_ids") or []) if str(x).strip()]
    supplementary_figure_ids = list(job.get("supplementary_figure_ids") or [])
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
            "step_chips": [
                {
                    "id": s,
                    "label": STEP_LABELS[s],
                    "meta": (job.get("component_meta") or {}).get(
                        s, {"status": "missing", "source": "none", "missing": []}
                    ),
                }
                for s in STEP_ORDER
            ],
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
            "prev_step": prev_step,
            "next_step": next_step,
            "progress": progress,
            "error": error,
            "info": info,
            "status_url": reverse("job_status", kwargs={"job_id": job_id}),
        },
    )


@login_required
def dashboard(request, job_id: str):
    job = get_job(job_id, user=request.user)
    if job is None:
        raise Http404("Unknown job id")

    figures_now = ((job.get("components") or {}).get("figures") or [])
    figure_ids_for_gt = [
        str(f.get("figure_id"))
        for f in figures_now
        if isinstance(f, dict) and str(f.get("figure_id") or "").strip()
    ]
    if not figure_ids_for_gt:
        paper_comp = (job.get("components") or {}).get("paper") or {}
        figure_ids_for_gt = [str(x) for x in (paper_comp.get("figure_ids") or []) if str(x).strip()]

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
    figure_media_rows = _figure_media_rows(components["figures"], components["paper"], job_id)
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
        }
    )
    return render(request, "researcher_ai_portal/dashboard.html", context)
