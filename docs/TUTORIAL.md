# Tutorial: Parsing a Publication End to End

This walkthrough takes you from submitting a paper to a complete parsed pipeline output. It assumes the portal is already running — see [`SETUP.md`](SETUP.md) if it isn't.

Baseline compatibility for this tutorial is pinned to `researcher-ai` `v3.0.0`.

---

## Overview

The portal runs a six-step parsing pipeline on any PubMed publication or PDF. Each step uses an LLM to extract structured data from the paper's text, figures, and supplementary material:

```
Paper → Figures → Method → Datasets → Software → Pipeline
```

Every step produces a validated JSON payload you can inspect and edit. Steps are sequential and dependency-aware: if you edit the Method step, the portal knows to re-run Datasets, Software, and Pipeline, but not Paper or Figures.

---

## Step 1 — Open the portal

Navigate to http://localhost:8000. You'll see the submission form with two tabs: **PubMed ID** and **Upload PDF**.

If Globus authentication is configured, sign in using the **Sign in with Globus** button before submitting. Without Globus, the portal creates a local guest session automatically.

---

## Step 2 — Submit a publication

### Using a PubMed ID

1. Click the **PubMed ID** tab.
2. Enter a PubMed ID, e.g. `35486828`.
3. Choose an LLM model from the dropdown (e.g. `claude-sonnet-4-6`, `gpt-5.4`, `gemini-2.5-pro`).
4. Enter your API key for the selected provider in the **API Key** field. The key is stored only in your browser session and is never written to the database.
5. Click **Start Parsing**.

### Using a PDF

1. Click the **Upload PDF** tab.
2. Drag and drop a PDF or click to browse.
3. Select a model and enter an API key.
4. Click **Start Parsing**.

PDFs are saved temporarily on the server during processing and are used to crop figure panels for multimodal analysis.

---

## Step 3 — Monitor progress

After submission you're redirected to the progress page. A progress bar updates as each step completes. Status messages appear below the bar, e.g. "Running Figure Parser — figure 3 of 12."

Parse logs are appended in real time. If a step fails, the error is shown with a plain-language explanation. Common causes: rate limits on the LLM provider, a figure with no parseable content, or a missing supplementary PDF.

---

## Step 4 — Review and edit each step

When parsing finishes, you're taken to the first workflow step: **Paper**. Use the **Next →** and **← Prev** navigation at the bottom of each step page to move through the pipeline.

### Step pages

Each step page shows:

- **Status badge** — `found` (parsed successfully), `inferred` (low-confidence fill), or `missing` (could not be extracted).
- **JSON editor** — the full structured output for this step, editable in-place. The editor validates your JSON against the underlying Pydantic model before saving.
- **Save** — saves edits without re-running. Use this for minor corrections.
- **Re-run** — discards the current payload and re-parses this step from scratch.
- **Save & Rebuild** — saves your edits, then re-runs all downstream steps that depend on this one.

### The Figure step

The Figure step has an additional **Figure Ground Truth** panel below the JSON editor. For each detected figure:

- View the proxied figure image.
- Override the plot type, axis labels, title, and caption.
- Mark figures you want excluded from method matching.
- Figure IDs are normalized before figure interpretation so aliases like `Figure 1`, `Fig. 1`, and `F1` collapse to one figure record.
  Primary and supplementary references stay separate (for example, `Figure 1` is not merged with `Figure S1` or `Supplementary Figure 1`).
- If a preview image fails to load, use **Open in popup** to view the source figure in a modal without leaving the page.

Ground truth corrections are injected into the figure payload and propagate to the Method and Pipeline steps during rebuild.

### The Method step

The Method step now includes an **Assay step outline** card above the JSON editor. It gives a plain-English view of each assay and every extracted step.

- Use **Correct this step** on any step to open a side drawer with editable fields.
- Empty constrained fields include example formats and dropdown suggestions (for example `2.7.11b`, `FASTQ.gz`, `sorted BAM`) so it's clear what shape to enter.
- The **Parameters** field in the sidebar uses JSON-object editing and must stay a dictionary (for example `{"threads": "16", "min_mapq": "30"}`).
- Update description, software, version, input/output data, parameters, and code reference without touching raw JSON.
- Save the correction to write changes directly into the structured `method.assay_graph.assays[*].steps[*]` payload.
- Step cards highlight related `parse_warnings` so you can resolve issues in context.
- If `template_missing_stages` appears, the UI shows inferred empty stage skeletons. You can fill and save those stages, or remove suggestions you do not need.
- You can also click **Remove all suggestions** at the assay level to batch-clear inferred template stages for that assay.
- You can check multiple rows in the assay outline and click **Remove selected** to batch-delete both real steps and inferred stage suggestions in one action.
- If you fill a suggested stage from the outline, the portal now maps virtual suggestion rows to a real appended step reliably (no step-index mismatch errors).
- Cryptic template warnings are translated inline. For example, `assay='iPSC neuron differentiation' template=generic missing=align` is shown as a plain-English message that the assay is missing the `align` stage from the generic template.
- The **Methods Parser data** JSON card is still available for full manual edits when needed.

---

## Step 5 — Open the dashboard

Click **Dashboard** in the top navigation (or navigate to `/jobs/<job_id>/dashboard/`) after all steps complete.

The dashboard shows:

- **Summary counts** — total figures, assays, datasets, software tools, and pipeline steps found.
- **Component quality** — bar chart of found / inferred / missing status across steps.
- **Pipeline topology** — the ordered sequence of pipeline steps.
- **Entity lists** — dataset accessions (GEO/SRA), software names, figure IDs.

### Assay DAG

If `dash-cytoscape` is installed, the dashboard includes an interactive assay dependency graph. Node colours indicate confidence: green (≥ 80%), yellow (≥ 50%), red (< 50%).

Click any assay node to open a detail panel showing associated software, figures, and detected nf-core / GitHub links.

### Overall confidence score

A 0–100 confidence score summarises extraction quality:

- **50%** — average step completeness (software found, version identified, I/O typed).
- **20%** — figure-to-assay matching confidence.
- **15%** — dataset accession resolution rate.
- **15%** — parse warning count (fewer warnings = higher score).

Use this score to triage where manual review is most needed.

### DAG-aware rebuild

If you edit any step after viewing the dashboard, click **Rebuild Pipeline** in the dashboard to re-run only the downstream steps affected by your change. This avoids re-running the slow Paper and Figures steps when you only edited a method step.

---

## Step 6 — Export results

Each step's JSON payload is the canonical output. To export:

- **Via browser** — open the JSON editor on any step page and copy the content.
- **Via API** — `GET /jobs/<job_id>/workflow/<step>/` returns the step page including the current payload.

The Portal does not yet have a one-click export of the full pipeline config to a Nextflow or Snakemake file, but the **Pipeline** step payload contains the full `PipelineConfig` object which can be serialised to those formats by the `researcher-ai` package directly.

---

## Working with the FastAPI layer

The portal exposes a REST API at `/api/v1/` for programmatic access. Authentication uses the same session cookie as the browser UI — log in through the browser first, then call the API with `credentials: "include"` (from JS) or by passing the `sessionid` cookie (from curl/Python).

### Endpoints available in Phase 1

```
GET /api/v1/ping                  Liveness check (no auth required)
GET /api/v1/jobs                  List your parse jobs
GET /api/v1/jobs/{job_id}         Retrieve a single job summary
```

### Interactive API documentation

Visit http://localhost:8000/api/v1/docs for the Swagger UI. All Phase 1 and later endpoints are documented there with request/response schemas.

### Example: list your jobs via curl

```bash
# First, get your session cookie from the browser (DevTools → Application → Cookies → sessionid)
SESSION_ID="your-sessionid-value-here"

curl -s http://localhost:8000/api/v1/jobs \
  -H "Cookie: sessionid=$SESSION_ID" | python -m json.tool
```

---

## Useful endpoints

| URL | Description |
|-----|-------------|
| `/` | Home page — submission form and recent jobs |
| `/healthz/` | Health check (returns `ok`) |
| `/jobs/<job_id>/` | Redirect to current workflow step |
| `/jobs/<job_id>/workflow/<step>/` | View/edit a specific step (`paper`, `figures`, `method`, `datasets`, `software`, `pipeline`) |
| `/jobs/<job_id>/status/` | Job status JSON (progress %, stage, error) |
| `/jobs/<job_id>/dashboard/` | Summary dashboard with DAG and figure gallery |
| `/api/v1/docs` | FastAPI interactive documentation |
| `/api/v1/ping` | FastAPI smoke test |

---

## Tips

**Re-use cached paper parses.** The portal caches Paper and Figures step results by PubMed ID and LLM model. Submitting the same paper again with the same model skips the expensive paper fetch and re-uses cached results. Check the **Force reparse** checkbox on the submission form to override this.

**Try a simpler model first.** For initial exploration, `gemini-2.5-pro` or `gpt-5.4-mini` is faster and cheaper. Switch to `claude-opus-4-1` or `gpt-5.4` if the method step produces low-quality or incomplete assay graphs.

**Use step correction first for Method edits.** The Assay step outline card is the safest place for common fixes. It keeps edits focused on one assay step and avoids JSON syntax mistakes. Use the raw JSON editor only when you need broader structural changes.

**Check the figure ground truth before rebuilding.** If a figure is misclassified (e.g. marked as a bar chart when it's a scatter plot), correcting it in the Figure Ground Truth panel and then triggering a rebuild will improve method-to-figure matching in downstream steps.
