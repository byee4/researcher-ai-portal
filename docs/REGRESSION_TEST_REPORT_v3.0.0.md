# Portal Regression Report (`researcher-ai` v3.0.0)

Date: 2026-04-11  
Scope: Non-live regression tests for `researcher-ai-portal` after upgrading the baseline package pin to `researcher-ai` tag `v3.0.0`.

## Installation command

```bash
. .venv/bin/activate
python -m pip install --upgrade "git+https://github.com/byee4/researcher-ai.git@v3.0.0"
```

Resolved package version:
- `researcher-ai==3.0.0`
- Git commit resolved by pip during install: `fd5e9039071b7308db95c4edb3f83dd4ab0c1c6e`

## Regression test command (non-live)

```bash
. .venv/bin/activate
python -m pytest researcher_ai_portal_app/tests -q
```

## Result

- `151 passed in 6.88s`
- No live/manual parse runs were included in this regression pass.

## Notes

- The first test attempt failed at collection due to missing `fastapi` in the active venv.
- After reinstalling portal requirements (`python -m pip install -r requirements.txt`), the full suite passed.
