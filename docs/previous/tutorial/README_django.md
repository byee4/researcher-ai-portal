# Researcher AI Django + Globus Template

Basic Django template wired for Globus Auth via
`django-globus-portal-framework` and `social-auth-app-django`.

## Quick start (Docker)

1. Optionally create a `.env` file from `.env.example` and fill in Globus values.
2. Run:

```bash
./scripts/run_local_docker.sh
```

3. Open http://localhost:8000
4. Sign in at http://localhost:8000/login/globus/

## Notes

- Default stack starts Postgres + Django web app.
- `settings.py` follows the environment-driven patterns from your `yeolab_kb` project.
- If `DATABASE_URL` is not set, app falls back to SQLite.
