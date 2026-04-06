from __future__ import annotations

import os

try:  # pragma: no cover - optional dependency in local MVP mode
    from celery import Celery
except Exception:  # pragma: no cover - fallback for environments without celery
    class _DummyCelery:
        def __init__(self, *args, **kwargs):
            pass

        def config_from_object(self, *args, **kwargs):
            return None

        def autodiscover_tasks(self, *args, **kwargs):
            return None

    Celery = _DummyCelery  # type: ignore[assignment]


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "researcher_ai_portal.settings")

app = Celery("researcher_ai_portal")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
