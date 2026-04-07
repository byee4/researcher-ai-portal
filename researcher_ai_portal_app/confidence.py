from __future__ import annotations

import re
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


def _slug(text: str) -> str:
    """Return a URL/CSS-safe slug for use as an ID fragment."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def compute_actionable_items(
    components: dict[str, Any],
    confidence_result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return a ranked list of concrete, fix-ready issues blocking pipeline confidence.

    Each item has this strict schema::

        {
            "id":                 str,         # stable dedup key
            "reason":             str,         # human-readable description
            "severity":           "high" | "medium" | "low",
            "confidence_impact":  float,       # estimated % gain if resolved
            "fix_target_tab":     str,         # dashboard tab name
            "fix_target_node_id": str | None,  # React Flow node ID (future)
            "fix_target_field":   str | None,  # CSS selector to focus
            "action_url":         str | None,  # fallback deep-link URL
            "fix_label":          str,         # CTA button text
        }

    Items are sorted by ``confidence_impact`` descending.
    """
    method = components.get("method") or {}
    assay_graph = (method.get("assay_graph") or {})
    assays = assay_graph.get("assays") or []
    parse_warnings = method.get("parse_warnings") or []
    assay_confidences = confidence_result.get("assay_confidences") or {}
    validation_passed = confidence_result.get("validation_passed", True)

    items: list[dict[str, Any]] = []

    for assay in assays:
        assay_name = str(assay.get("name") or "Unknown Assay")
        assay_slug = _slug(assay_name)
        steps = assay.get("steps") or []
        n_steps = max(len(steps), 1)
        assay_conf = assay_confidences.get(assay_name) or {}
        step_confs = assay_conf.get("step_confidences") or []

        # --- Per-step field issues (weight: 50% / 4 factors / n_steps) ---
        # Impact per missing factor per step = 12.5 / n_steps
        per_factor_impact = round(12.5 / n_steps, 2)

        for step_idx, step in enumerate(steps):
            sc = step_confs[step_idx] if step_idx < len(step_confs) else {}
            software_name = str(step.get("software") or "")
            step_slug = f"{assay_slug}-step-{step_idx}"

            if not sc.get("has_software", bool(software_name)):
                items.append({
                    "id": f"missing-software-{step_slug}",
                    "reason": f'No software recorded for step {step_idx + 1} of "{assay_name}"',
                    "severity": "high",
                    "confidence_impact": per_factor_impact,
                    "fix_target_tab": "editing",
                    "fix_target_node_id": None,
                    "fix_target_field": f"#id-step-{step_slug}-software",
                    "action_url": None,
                    "fix_label": "Add software name",
                })

            if not sc.get("has_version", bool(step.get("software_version"))):
                label = f'"{software_name}"' if software_name else f"step {step_idx + 1}"
                items.append({
                    "id": f"missing-version-{step_slug}",
                    "reason": f'Missing version for {label} in "{assay_name}"',
                    "severity": "high",
                    "confidence_impact": per_factor_impact,
                    "fix_target_tab": "editing",
                    "fix_target_node_id": None,
                    "fix_target_field": f"#id-step-{step_slug}-version",
                    "action_url": None,
                    "fix_label": "Add version number",
                })

            if not sc.get("has_parameters", bool(step.get("parameters"))):
                label = f'"{software_name}"' if software_name else f"step {step_idx + 1}"
                items.append({
                    "id": f"missing-params-{step_slug}",
                    "reason": f'No parameters for {label} in "{assay_name}"',
                    "severity": "medium",
                    "confidence_impact": per_factor_impact,
                    "fix_target_tab": "editing",
                    "fix_target_node_id": None,
                    "fix_target_field": f"#id-step-{step_slug}-parameters",
                    "action_url": None,
                    "fix_label": "Add parameters",
                })

            if not sc.get("has_input_output",
                          bool(step.get("input_data") and step.get("output_data"))):
                items.append({
                    "id": f"missing-io-{step_slug}",
                    "reason": f'Missing input/output for step {step_idx + 1} of "{assay_name}"',
                    "severity": "medium",
                    "confidence_impact": per_factor_impact,
                    "fix_target_tab": "editing",
                    "fix_target_node_id": None,
                    "fix_target_field": f"#id-step-{step_slug}-input-data",
                    "action_url": None,
                    "fix_label": "Add input & output",
                })

        # --- Dataset resolution (weight: 15%, gain = (100-30)*0.15 = 10.5%) ---
        if not assay_conf.get("dataset_resolved", True):
            items.append({
                "id": f"unresolved-dataset-{assay_slug}",
                "reason": f'No dataset accession linked to "{assay_name}"',
                "severity": "high",
                "confidence_impact": 10.5,
                "fix_target_tab": "datasets",
                "fix_target_node_id": None,
                "fix_target_field": None,
                "action_url": None,
                "fix_label": "Review datasets",
            })

        # --- Figure evidence (weight: 20%, low evidence if mean < 60) ---
        fig_mean = assay_conf.get("figure_confidence_mean", 50.0)
        if fig_mean < 60.0:
            impact = round((60.0 - fig_mean) / 100.0 * 20.0, 2)
            items.append({
                "id": f"weak-figures-{assay_slug}",
                "reason": f'Weak figure evidence for "{assay_name}" ({fig_mean:.0f}% mean confidence)',
                "severity": "medium",
                "confidence_impact": impact,
                "fix_target_tab": "figures",
                "fix_target_node_id": None,
                "fix_target_field": None,
                "action_url": None,
                "fix_label": "Review figures",
            })

        # --- Parse warnings (weight: 15%, -25% per warning) ---
        warning_count = sum(
            1 for w in parse_warnings if assay_name.lower() in str(w).lower()
        )
        if warning_count > 0:
            impact = round(min(warning_count * 25.0, 100.0) * 0.15, 2)
            items.append({
                "id": f"parse-warnings-{assay_slug}",
                "reason": f'{warning_count} parse warning{"s" if warning_count > 1 else ""} for "{assay_name}"',
                "severity": "high" if warning_count > 1 else "medium",
                "confidence_impact": impact,
                "fix_target_tab": "workflow-graph",
                "fix_target_node_id": None,
                "fix_target_field": None,
                "action_url": "workflow_step:method",
                "fix_label": "Re-run method step",
            })

    # --- Validation failure ---
    if not validation_passed:
        items.append({
            "id": "validation-failed",
            "reason": "Pipeline validation failed — check component JSON for errors",
            "severity": "high",
            "confidence_impact": 5.0,
            "fix_target_tab": "advanced",
            "fix_target_node_id": None,
            "fix_target_field": None,
            "action_url": None,
            "fix_label": "Inspect raw JSON",
        })

    # Sort: highest confidence_impact first, then severity order
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda x: (-x["confidence_impact"], severity_rank.get(x["severity"], 3)))
    return items


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
