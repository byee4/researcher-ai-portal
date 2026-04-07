# Tutorial: Parse a Paper End-to-End in researcher-ai-portal

This tutorial walks through using the Django portal to run the `researcher-ai` parsing pipeline and inspect results in the dashboard.

## Architecture at a glance

- Django project module: `researcher_ai_portal`
- Django app: `researcher_ai_portal_app`
- Workflow runner script: `researcher-ai/scripts/run_workflow.py`
- Core package used by runner: `researcher-ai/researcher_ai/*`

The portal launches parsing in a background thread and subprocess, then streams progress to the browser.

## Step 1: Environment setup

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate researcher-ai
cd /Users/brianyee/Documents/work/01_active/researcher-ai
python -m pip install -r requirements.txt
python -m pip install -e ./researcher-ai
python -m pip install dpd-static-support
```

## Step 2: Start Django

```bash
cd /Users/brianyee/Documents/work/01_active/researcher-ai/researcher_ai
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

Go to [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Step 3: Launch a parse job

Use one of:

- PubMed ID (example: `26971820`)
- PDF upload (`.pdf`)

Click **Start Parsing**.

### What happens under the hood

1. Django creates a job record in memory (`job_store.py`).
2. A background worker starts the parser workflow:
   - `python researcher-ai/scripts/run_workflow.py ...`
3. The runner emits stage lines:
   - `PROGRESS|15|Parsing paper`
   - `PROGRESS|35|Parsing figures`
   - etc.
4. The progress endpoint (`/jobs/<job_id>/status/`) is polled by the browser.
5. On completion, JSON output is saved and dashboard page is opened.

## Step 4: Track progress

Progress page URL pattern:

- `/jobs/<job_id>/`

It shows:

- Percent complete
- Current stage text
- Error output if the workflow fails

## Step 5: Explore dashboard output

Dashboard URL pattern:

- `/jobs/<job_id>/dashboard/`

It includes:

- Plotly Dash summary panels (figures, assays, datasets, software, steps)
- Parsed title and paper type
- Expandable JSON sections:
  - Paper
  - Figures
  - Method
  - Datasets
  - Software
  - Pipeline

## Step 6: Common issues and fixes

### `ModuleNotFoundError: django`

Install requirements in the active env:

```bash
python -m pip install -r /Users/brianyee/Documents/work/01_active/researcher-ai/requirements.txt
```

### `No module named dpd_static_support`

```bash
python -m pip install dpd-static-support
```

### Progress stalls or job fails

- Check network/API availability for external data sources.
- Inspect the error block on the progress page.
- Re-run with a known PMID before trying arbitrary PDFs.

### Globus login not configured

Set env vars:

- `GLOBUS_CLIENT_ID`
- `GLOBUS_CLIENT_SECRET`
- `SOCIAL_AUTH_GLOBUS_REDIRECT_URI`

For local-only development, you can focus on authenticated sessions already configured in your environment.

## Step 7: Run with Docker

```bash
cd /Users/brianyee/Documents/work/01_active/researcher-ai
docker compose up --build
```

The compose web service points to:

- `researcher_ai_portal.wsgi:application`

## Where parsed outputs are stored

Portal-run outputs are written under:

- `researcher_ai/parse_results/<job_id>.json`

These files can be archived or post-processed for downstream analysis.
