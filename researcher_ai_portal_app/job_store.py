from __future__ import annotations

import threading
import uuid
from typing import Any

from django.contrib.auth import get_user_model

from .models import ComponentSnapshot, WorkflowJob

_LOCK = threading.Lock()
_FALLBACK_JOBS: dict[str, dict[str, Any]] = {}


def _job_to_dict(job: WorkflowJob) -> dict[str, Any]:
    comp_rows = ComponentSnapshot.objects.filter(job=job)
    components: dict[str, Any] = {}
    component_meta: dict[str, Any] = {}
    for row in comp_rows:
        components[row.step] = row.payload
        component_meta[row.step] = {
            "status": row.status,
            "missing": row.missing_fields,
            "source": row.source,
        }
    return {
        "job_id": str(job.id),
        "status": job.status,
        "progress": job.progress,
        "stage": job.stage,
        "error": job.error,
        "input_type": job.source_type,
        "input_value": job.input_display,
        "source": job.source,
        "source_type": job.source_type,
        "llm_model": job.llm_model,
        "result": None,
        "components": components,
        "component_meta": component_meta,
        "current_step": job.current_step,
        "figure_parse_total": job.figure_parse_total,
        "figure_parse_current": job.figure_parse_current,
        "supplementary_figure_ids": job.supplementary_figure_ids,
        "parse_logs": job.parse_logs,
        "user_id": job.user_id,
    }


def _resolve_user_for_creation(extra_fields: dict[str, Any]):
    user = extra_fields.pop("user", None)
    if user is not None:
        return user
    user_id = extra_fields.pop("user_id", None)
    if user_id is not None:
        try:
            return get_user_model().objects.get(pk=user_id)
        except Exception:
            return None
    try:
        return get_user_model().objects.order_by("id").first()
    except Exception:
        return None


def create_job(input_type: str, input_value: str, **extra_fields: Any) -> str:
    user = _resolve_user_for_creation(extra_fields)
    if user is None:
        # Backward-compatible fallback for non-Django/unit-test contexts.
        job_id = uuid.uuid4().hex
        with _LOCK:
            _FALLBACK_JOBS[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "progress": 0,
                "stage": "Queued",
                "error": "",
                "input_type": input_type,
                "input_value": input_value,
                "result": None,
                "components": extra_fields.pop("components", {}) or {},
                "component_meta": extra_fields.pop("component_meta", {}) or {},
                "current_step": "paper",
                **extra_fields,
            }
        return job_id

    llm_model = str(extra_fields.pop("llm_model", "gpt-5.4") or "gpt-5.4")
    source = str(extra_fields.pop("source", input_value))
    source_type = str(extra_fields.pop("source_type", input_type))
    status = str(extra_fields.pop("status", "queued"))
    stage = str(extra_fields.pop("stage", "Queued"))
    progress = int(extra_fields.pop("progress", 0) or 0)
    current_step = str(extra_fields.pop("current_step", "paper"))
    error = str(extra_fields.pop("error", "") or "")
    figure_parse_total = int(extra_fields.pop("figure_parse_total", 0) or 0)
    figure_parse_current = int(extra_fields.pop("figure_parse_current", 0) or 0)
    supplementary_figure_ids = extra_fields.pop("supplementary_figure_ids", []) or []
    parse_logs = extra_fields.pop("parse_logs", []) or []

    # Intentionally ignored (security): llm_api_key is session/task scoped only.
    extra_fields.pop("llm_api_key", None)

    try:
        job = WorkflowJob.objects.create(
            user=user,
            source=source,
            source_type=source_type,
            input_display=input_value,
            llm_model=llm_model,
            status=status,
            stage=stage,
            progress=progress,
            current_step=current_step,
            error=error,
            figure_parse_total=figure_parse_total,
            figure_parse_current=figure_parse_current,
            supplementary_figure_ids=supplementary_figure_ids,
            parse_logs=parse_logs,
        )
    except Exception:
        # Fallback in case migrations/db are not available in a lightweight context.
        job_id = uuid.uuid4().hex
        with _LOCK:
            _FALLBACK_JOBS[job_id] = {
                "job_id": job_id,
                "status": status,
                "progress": progress,
                "stage": stage,
                "error": error,
                "input_type": input_type,
                "input_value": input_value,
                "source": source,
                "source_type": source_type,
                "llm_model": llm_model,
                "result": None,
                "components": extra_fields.pop("components", {}) or {},
                "component_meta": extra_fields.pop("component_meta", {}) or {},
                "current_step": current_step,
                "figure_parse_total": figure_parse_total,
                "figure_parse_current": figure_parse_current,
                "supplementary_figure_ids": supplementary_figure_ids,
                "parse_logs": parse_logs,
                "user_id": getattr(user, "id", None),
            }
        return job_id

    components = extra_fields.pop("components", {}) or {}
    component_meta = extra_fields.pop("component_meta", {}) or {}
    for step, payload in components.items():
        meta = component_meta.get(step, {}) if isinstance(component_meta, dict) else {}
        ComponentSnapshot.objects.update_or_create(
            job=job,
            step=str(step),
            defaults={
                "payload": payload,
                "status": str(meta.get("status", "found")),
                "missing_fields": meta.get("missing", []) or [],
                "source": str(meta.get("source", "parsed")),
            },
        )
    return str(job.id)


def update_job(job_id: str, user=None, **fields: Any) -> None:
    if job_id in _FALLBACK_JOBS:
        with _LOCK:
            _FALLBACK_JOBS[job_id].update(fields)
        return

    qs = WorkflowJob.objects.filter(id=job_id)
    if user is not None:
        qs = qs.filter(user=user)
    job = qs.first()
    if job is None:
        return

    components = fields.pop("components", None)
    component_meta = fields.pop("component_meta", None) or {}
    fields.pop("llm_api_key", None)

    mutable_fields = {
        "status",
        "progress",
        "stage",
        "error",
        "current_step",
        "figure_parse_total",
        "figure_parse_current",
        "supplementary_figure_ids",
        "parse_logs",
        "llm_model",
        "source",
        "source_type",
    }
    update_kwargs = {k: v for k, v in fields.items() if k in mutable_fields}
    if update_kwargs:
        for k, v in update_kwargs.items():
            setattr(job, k, v)
        job.save(update_fields=list(update_kwargs.keys()) + ["updated_at"])

    if components is not None and isinstance(components, dict):
        for step, payload in components.items():
            meta = component_meta.get(step, {}) if isinstance(component_meta, dict) else {}
            ComponentSnapshot.objects.update_or_create(
                job=job,
                step=str(step),
                defaults={
                    "payload": payload,
                    "status": str(meta.get("status", "found")),
                    "missing_fields": meta.get("missing", []) or [],
                    "source": str(meta.get("source", "parsed")),
                },
            )


def get_job(job_id: str, user=None) -> dict[str, Any] | None:
    """Return a job snapshot.

    Notes:
    - View-layer callers must pass ``user=request.user`` for isolation.
    - ``user=None`` is intentionally supported for backend/Celery task access
      where the worker operates on jobs across users by explicit job id.
    """
    if job_id in _FALLBACK_JOBS:
        with _LOCK:
            job = dict(_FALLBACK_JOBS.get(job_id) or {})
        if user is not None and job.get("user_id") not in (None, getattr(user, "id", None)):
            return None
        return job

    qs = WorkflowJob.objects.filter(id=job_id)
    if user is not None:
        qs = qs.filter(user=user)
    job = qs.first()
    if job is None:
        return None
    return _job_to_dict(job)
