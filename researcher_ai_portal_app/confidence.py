from __future__ import annotations

from statistics import mean
from typing import Any


def _figure_confidence_by_assay(figures: list[dict], assays: list[dict]) -> dict[str, float]:
    assay_names = [str(a.get("name") or "").strip() for a in assays if str(a.get("name") or "").strip()]
    if not assay_names:
        return {}

    scores: dict[str, list[float]] = {name: [] for name in assay_names}
    for fig in figures or []:
        caption = str(fig.get("caption") or "")
        title = str(fig.get("title") or "")
        purpose = str(fig.get("purpose") or "")
        blob = f"{title}\n{caption}\n{purpose}".lower()
        subfigures = fig.get("subfigures") or []
        sub_scores: list[float] = []
        for sf in subfigures:
            if not isinstance(sf, dict):
                continue
            raw = sf.get("composite_confidence")
            if raw is None:
                raw = float(sf.get("classification_confidence") or 0.5) * 100.0
            sub_scores.append(float(raw))
        fig_conf = mean(sub_scores) if sub_scores else 50.0
        for name in assay_names:
            if name.lower() in blob:
                scores[name].append(fig_conf)
    return {name: round(mean(vals), 1) if vals else 50.0 for name, vals in scores.items()}


def compute_confidence(components: dict[str, Any]) -> dict[str, Any]:
    """Compute pipeline confidence from parsed component JSON payloads."""
    method = components.get("method") or {}
    figures = components.get("figures") or []
    datasets = components.get("datasets") or []
    pipeline = components.get("pipeline") or {}

    assay_graph = (method.get("assay_graph") or {})
    assays = assay_graph.get("assays") or []
    parse_warnings = method.get("parse_warnings") or []
    dataset_accessions = {
        str(d.get("accession") or "").upper()
        for d in datasets
        if isinstance(d, dict) and str(d.get("accession") or "").strip()
    }
    fig_conf_by_assay = _figure_confidence_by_assay(figures, assays)

    assay_confidences: dict[str, dict[str, Any]] = {}
    weighted_sum = 0.0
    total_steps = 0

    for assay in assays:
        name = str(assay.get("name") or "Unknown Assay")
        steps = assay.get("steps") or []
        step_confidences: list[dict[str, Any]] = []
        for step in steps:
            has_software = bool(step.get("software"))
            has_version = bool(step.get("software_version"))
            has_parameters = bool(step.get("parameters"))
            has_input_output = bool(step.get("input_data") and step.get("output_data"))
            completeness = sum([has_software, has_version, has_parameters, has_input_output]) / 4.0
            step_confidences.append(
                {
                    "has_software": has_software,
                    "has_version": has_version,
                    "has_parameters": has_parameters,
                    "has_input_output": has_input_output,
                    "parameter_completeness": round(completeness, 2),
                    "overall": round(completeness * 100.0, 1),
                }
            )

        raw_source = str(assay.get("raw_data_source") or "").upper()
        dataset_resolved = any(acc and acc in raw_source for acc in dataset_accessions) if dataset_accessions else False
        warning_count = sum(1 for w in parse_warnings if name.lower() in str(w).lower())
        step_mean = mean([c["overall"] for c in step_confidences]) if step_confidences else 50.0
        figure_mean = fig_conf_by_assay.get(name, 50.0)
        overall = (
            step_mean * 0.50
            + figure_mean * 0.20
            + (100.0 if dataset_resolved else 30.0) * 0.15
            + max(0.0, 100.0 - warning_count * 25.0) * 0.15
        )
        assay_confidences[name] = {
            "step_confidences": step_confidences,
            "dataset_resolved": dataset_resolved,
            "figure_confidence_mean": round(figure_mean, 1),
            "parse_warning_count": warning_count,
            "overall": round(overall, 1),
        }
        weight = max(len(steps), 1)
        weighted_sum += overall * weight
        total_steps += weight

    validation_passed = bool(((pipeline.get("validation_report") or {}).get("passed", True)))
    overall = round(weighted_sum / max(total_steps, 1), 1)
    return {
        "assay_confidences": assay_confidences,
        "validation_passed": validation_passed,
        "overall": overall,
        "human_edited_steps": 0,
    }
