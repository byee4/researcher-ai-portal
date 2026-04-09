# Orchestrator-First Portal Integration (v2)

## Summary
Promote orchestrator mode to the default runtime, preserve richer orchestrator outputs in `job_metadata`, and relax dataset normalization so subtype-specific fields survive. Keep legacy mode as an explicit fallback, with guardrails for payload size and compatibility drift.

## Key Changes
1. **Runner default + compatibility**
1. Change `_runner_mode()` default from `legacy` to `orchestrator`.
1. Keep `RESEARCHER_AI_PORTAL_RUNNER_MODE=legacy` as rollback override.
1. Improve drift observability:
1. If `RESEARCHER_AI_EXPECTED_VERSION` is set and mismatched, keep warning.
1. If unset, log one informational notice per run that drift checks are disabled.
1. Update docs/env examples to show orchestrator-first default and recommended version pinning.

2. **Persist orchestrator diagnostics safely**
1. Extend orchestrator normalization/persist path to store these in `job_metadata`:
`dataset_parse_errors`, `workflow_graph_validation_issues`, `method_validation_report`, `validation_blocked`, `build_attempts`, `max_build_attempts`, final `stage`, final `progress`.
1. Add metadata compaction before persistence:
1. Truncate long strings (default 2,000 chars each).
1. Cap list lengths (default 100 items per diagnostic list).
1. Recursively cap nested dict/list depth (default depth 6).
1. Preserve structured keys while replacing truncated values with `"...truncated"` markers.
1. Keep existing component validation for canonical components unchanged.

3. **Preserve dataset subtype richness**
1. Adjust dataset validation/normalization so base contract is enforced (`accession`, `source` semantics) but orchestrator extra keys are retained.
1. Ensure round-trip persistence does not drop subtype fields (for example PRIDE/proteomics extras).
1. Maintain backward compatibility for existing UI fields (`accession`, `source`/`source_type`) and older records.

4. **API/status behavior**
1. Keep existing response shapes; only add metadata fields.
1. Ensure status and dashboard context continue to function when diagnostics are absent, partial, or large.

## Tests
1. **Runner behavior**
1. Default mode resolves to orchestrator.
1. Invalid mode still falls back deterministically.

2. **Metadata persistence + compaction**
1. Orchestrator diagnostics persist into `job_metadata`.
1. Oversized diagnostics are compacted per truncation policy.
1. Missing optional orchestrator fields do not fail job completion.

3. **Dataset round-trip**
1. Enriched dataset dicts with subtype keys survive validate→persist→read.
1. Legacy-minimal dataset payloads remain valid and unchanged.

4. **Status/dashboard regressions**
1. `job_status` and dashboard context handle enriched metadata and human-review paths.
1. Large mocked diagnostics serialize within acceptable latency budget (target: under 500ms in test environment).

## Assumptions & Defaults
1. No API-breaking removals; additions are backward compatible.
1. Compaction thresholds use defaults above unless product requirements specify stricter limits.
1. Legacy mode remains supported but no longer default.
1. PXD/PRIDE extraction logic itself is handled in a separate task.
