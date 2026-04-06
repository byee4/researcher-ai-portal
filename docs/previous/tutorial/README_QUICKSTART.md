# researcher-ai-portal Quickstart

This quickstart gets the Django portal running and wired to the `researcher-ai` package workflow.

## Prerequisites

- Python 3.11+
- Conda env: `~/miniconda3/envs/researcher-ai`
- Network access for PubMed/PMC/API-backed parsing steps
- Optional: Globus OAuth client values (for full auth flow)

## 1) Activate environment

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate researcher-ai
```

## 2) Install dependencies

From repository root:

```bash
cd /Users/brianyee/Documents/work/01_active/researcher-ai
python -m pip install -r requirements.txt
python -m pip install -e ./researcher-ai
python -m pip install dpd-static-support
```

## 3) Configure environment (optional but recommended)

Create `.env` in repo root for Django settings if needed:

```env
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_SECRET_KEY=dev-secret-key

# Optional Globus:
GLOBUS_CLIENT_ID=
GLOBUS_CLIENT_SECRET=
SOCIAL_AUTH_GLOBUS_REDIRECT_URI=
```

## 4) Run Django checks and migrations

```bash
cd /Users/brianyee/Documents/work/01_active/researcher-ai/researcher_ai
python manage.py check
python manage.py migrate
python manage.py collectstatic --noinput
```

## 5) Start the portal

```bash
python manage.py runserver 0.0.0.0:8000
```

Open:

- [http://127.0.0.1:8000](http://127.0.0.1:8000)
- Health: [http://127.0.0.1:8000/healthz/](http://127.0.0.1:8000/healthz/)

## 6) Parse a paper

On the home page:

1. Enter a PubMed ID, or upload a PDF.
2. Click **Start Parsing**.
3. Watch progress bar updates.
4. When complete, view the dashboard and parsed JSON sections.

## Docker option

```bash
cd /Users/brianyee/Documents/work/01_active/researcher-ai
docker compose up --build
```

The web service uses `researcher_ai_portal.wsgi:application`.

