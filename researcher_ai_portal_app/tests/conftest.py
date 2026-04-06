from __future__ import annotations

import os
import sys
from pathlib import Path

import django


PORTAL_ROOT = Path(__file__).resolve().parents[2]
if str(PORTAL_ROOT) not in sys.path:
    sys.path.insert(0, str(PORTAL_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "researcher_ai_portal.settings")
django.setup()
