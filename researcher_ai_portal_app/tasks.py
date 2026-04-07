from __future__ import annotations

from typing import Any

from django.core.cache import cache

from .job_events import append_job_log, merge_logs
from .job_store import get_job, update_job

try:  # pragma: no cover - optional dependency in local MVP mode
    from celery import shared_task
except Exception:  # pragma: no cover - fallback for environments without celery
    def shared_task(*dargs, **dkwargs):
        bind = bool(dkwargs.get("bind", False))

        def decorator(fn):
            def _delay(*args, **kwargs):
                if bind:
                    return fn(None, *args, **kwargs)
                return fn(*args, **kwargs)

            fn.delay = _delay  # type: ignore[attr-defined]
            return fn

        return decorator


def _cache_key(job_id: str) -> str:
    return f"job_progress:{job_id}"


@shared_task(bind=True, max_retries=0)
def run_workflow_step(
    self,
    job_id: str,
    step: str,
    llm_api_key: str = "",
    llm_model: str = "",
    force_reparse: bool = False,
) -> dict[str, Any]:
    from .views import STEP_LABELS, _progress_for_step, _run_step

    label = STEP_LABELS.get(step, step)
    job = get_job(job_id)
    user_id = (job or {}).get("user_id")
    cache_payload = {
        "status": "in_progress",
        "progress": _progress_for_step(step),
        "stage": f"Running {label}",
        "current_step": step,
        "figure_parse_current": 0,
        "figure_parse_total": 0,
        "user_id": user_id,
    }
    cache.set(_cache_key(job_id), merge_logs(cache_payload, job_id), timeout=3600)
    append_job_log(job_id, f"Worker started step: {label}", step=step)

    try:
        update_job(job_id, status="in_progress", current_step=step, stage=f"Running {label}")
        _run_step(
            job_id,
            step,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            force_reparse=force_reparse,
        )
        progress = _progress_for_step(step)
        update_job(job_id, status="in_progress", current_step=step, progress=progress, stage=f"Completed {label}")
        append_job_log(job_id, f"Step completed: {label}", step=step)
        complete_payload = {
            "status": "step_complete",
            "progress": progress,
            "stage": f"Completed {label}",
            "current_step": step,
            "user_id": user_id,
        }
        cache.set(_cache_key(job_id), merge_logs(complete_payload, job_id), timeout=3600)
        return {"ok": True, "job_id": job_id, "step": step}
    except Exception as exc:
        update_job(job_id, status="failed", current_step=step, stage=f"{label} failed", error=str(exc))
        append_job_log(job_id, f"Step failed: {label}: {exc}", level="error", step=step)
        fail_payload = {
            "status": "failed",
            "progress": (get_job(job_id) or {}).get("progress", 0),
            "stage": f"{label} failed",
            "current_step": step,
            "error": str(exc),
            "user_id": user_id,
        }
        cache.set(_cache_key(job_id), merge_logs(fail_payload, job_id), timeout=3600)
        return {"ok": False, "job_id": job_id, "step": step, "error": str(exc)}


@shared_task(bind=True, max_retries=0)
def rebuild_from_step(
    self,
    job_id: str,
    edited_step: str,
    llm_api_key: str = "",
    llm_model: str = "",
) -> dict[str, Any]:
    from .views import STEP_ORDER, invalidated_steps

    dirty = invalidated_steps(get_job(job_id) or {}, edited_step)
    for step in dirty:
        run_workflow_step(job_id, step, llm_api_key=llm_api_key, llm_model=llm_model)
    final_step = dirty[-1] if dirty else edited_step
    update_job(job_id, status="in_progress", current_step=final_step)
    return {"ok": True, "job_id": job_id, "rebuild_steps": dirty}
