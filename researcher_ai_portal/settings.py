"""
Django settings for a minimal Globus-auth-enabled template app.

This mirrors the yeolab_kb settings style with environment-driven config,
Globus/social-auth integration, and Docker-friendly defaults.
"""

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

_dotenv_path = BASE_DIR.parent / '.env'
if _dotenv_path.is_file():
    load_dotenv(_dotenv_path)

SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-template-dev-key-change-in-production',
)

DEBUG = os.environ.get('DJANGO_DEBUG', 'True').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get('DJANGO_ALLOWED_HOSTS', '*').split(',')
    if h.strip()
]


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in ('true', '1', 'yes', 'on')


def _env_list(name: str) -> list[str]:
    return [v.strip() for v in os.environ.get(name, '').split(',') if v.strip()]


INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.staticfiles',
    'django_plotly_dash.apps.DjangoPlotlyDashConfig',
    'django_svelte_jsoneditor',
    'dpd_static_support',
    'social_django',
    'globus_portal_framework',
    'researcher_ai_portal_app',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django_plotly_dash.middleware.BaseMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'social_django.middleware.SocialAuthExceptionMiddleware',
    'globus_portal_framework.middleware.ExpiredTokenMiddleware',
    'globus_portal_framework.middleware.GlobusAuthExceptionMiddleware',
]

AUTHENTICATION_BACKENDS = [
    'social_core.backends.globus.GlobusOpenIdConnect',
    'globus_portal_framework.auth.GlobusOpenIdConnect',
    'django.contrib.auth.backends.ModelBackend',
]

ROOT_URLCONF = 'researcher_ai_portal.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'social_django.context_processors.backends',
                'social_django.context_processors.login_redirect',
                'globus_portal_framework.context_processors.globals',
            ],
        },
    },
]

WSGI_APPLICATION = 'researcher_ai_portal.wsgi.application'

DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(DATABASE_URL, conn_max_age=600),
    }
    DATABASES['default']['CONN_HEALTH_CHECKS'] = True
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': os.environ.get(
                'TEMPLATE_DB_PATH',
                # Keep local SQLite DB inside the project root so dev/test
                # environments (including sandboxed runs) can write safely.
                str((BASE_DIR / 'template.db').resolve()),
            ),
        }
    }

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'America/Los_Angeles'
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
STORAGES = {
    'staticfiles': {
        'BACKEND': 'researcher_ai_portal.staticfiles.StableStaticFilesStorage',
    },
}
STATICFILES_FINDERS = [
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
    'django_plotly_dash.finders.DashAssetFinder',
    'django_plotly_dash.finders.DashComponentFinder',
    'django_plotly_dash.finders.DashAppDirectoryFinder',
]
PLOTLY_COMPONENTS = [
    'dpd_components',
    'dpd_static_support',
]

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "researcher-ai-portal-local-cache",
    }
}

SESSION_ENGINE = os.environ.get(
    "SESSION_ENGINE",
    "django.contrib.sessions.backends.db",
)
SESSION_CACHE_ALIAS = "default"

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

SOCIAL_AUTH_GLOBUS_KEY = os.environ.get('GLOBUS_CLIENT_ID', '').strip()
SOCIAL_AUTH_GLOBUS_SECRET = os.environ.get('GLOBUS_CLIENT_SECRET', '').strip()
GLOBUS_ADMIN_GROUP = os.environ.get('GLOBUS_ADMIN_GROUP', '').strip()

SOCIAL_AUTH_GLOBUS_SCOPE = [
    'openid',
    'profile',
    'email',
    'urn:globus:auth:scope:groups.api.globus.org:view_my_groups_and_memberships',
]
SOCIAL_AUTH_GLOBUS_IGNORE_DEFAULT_SCOPE = True
SOCIAL_AUTH_GLOBUS_SESSIONS = True
SOCIAL_AUTH_GLOBUS_ALLOWED_GROUPS = (
    [{'name': 'Admin Group', 'uuid': GLOBUS_ADMIN_GROUP}] if GLOBUS_ADMIN_GROUP else []
)
SOCIAL_AUTH_REDIRECT_IS_HTTPS = _env_bool('SOCIAL_AUTH_REDIRECT_IS_HTTPS', not DEBUG)

SOCIAL_AUTH_GLOBUS_REDIRECT_URI = os.environ.get(
    'SOCIAL_AUTH_GLOBUS_REDIRECT_URI',
    '',
).strip()

LOGIN_URL = '/login/globus/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

SOCIAL_AUTH_PIPELINE = (
    'social_core.pipeline.social_auth.social_details',
    'social_core.pipeline.social_auth.social_uid',
    'social_core.pipeline.social_auth.auth_allowed',
    'social_core.pipeline.social_auth.social_user',
    'social_core.pipeline.user.get_username',
    'social_core.pipeline.user.create_user',
    'social_core.pipeline.social_auth.associate_user',
    'social_core.pipeline.social_auth.load_extra_data',
    'social_core.pipeline.user.user_details',
)

CSRF_TRUSTED_ORIGINS = _env_list('CSRF_TRUSTED_ORIGINS')
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_PORT = True

# ---------------------------------------------------------------------------
# HuggingFace Hub — suppress unauthenticated-request warnings from the
# sentence-transformers model loaded by the researcher-ai RAG layer.
# HF_HUB_DISABLE_IMPLICIT_TOKEN tells the hub client not to warn about
# missing tokens when running in an environment without one.
# TOKENIZERS_PARALLELISM=false silences the tokenizer fork-safety warning
# that appears in forked worker processes (e.g. Celery / Gunicorn prefork).
# ---------------------------------------------------------------------------
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# ---------------------------------------------------------------------------
# researcher-ai runtime defaults for the portal baseline release.
# These values are applied only when the variable is unset in the environment.
# ---------------------------------------------------------------------------
_RESEARCHER_AI_DEFAULTS = {
    # Keep orchestrator generous, not infinite.
    "RESEARCHER_AI_PARSE_FIGURES_TIMEOUT_SECONDS": "1800",
    "RESEARCHER_AI_PARSE_FIGURES_TIMEOUT_PER_FIGURE_SECONDS": "180",
    # Bound per-call latency so runs finish.
    "RESEARCHER_AI_LLM_TIMEOUT_SECONDS": "180",
    "RESEARCHER_AI_SUBFIGURE_TIMEOUT_SECONDS": "180",
    "RESEARCHER_AI_MAX_FIGURE_LLM_TIMEOUTS": "4",
    "RESEARCHER_AI_PROVIDER_MAX_RETRIES": "2",
    "RESEARCHER_AI_PORTAL_STUCK_JOB_TIMEOUT_SECONDS": "3600",
    # Keep quality decent while controlling long-tail cost/latency.
    "RESEARCHER_AI_SUBFIGURE_DECOMPOSE_MAX_TOKENS": "1800",
    "RESEARCHER_AI_FIGURE_PURPOSE_MAX_TOKENS": "900",
    "RESEARCHER_AI_FIGURE_METHODS_DATASETS_MAX_TOKENS": "700",
    "RESEARCHER_AI_MAX_RETRIEVAL_REFINEMENT_ROUNDS": "3",
    "RESEARCHER_AI_BIOWORKFLOW_MODE": "warn",
}

for _name, _value in _RESEARCHER_AI_DEFAULTS.items():
    os.environ.setdefault(_name, _value)

# ---------------------------------------------------------------------------
# Logging
#
# Suppresses noisy third-party INFO/WARNING output that is not actionable:
#
#  sentence_transformers / transformers
#    - BertModel LOAD REPORT (embeddings.position_ids UNEXPECTED) — benign
#      when loading a checkpoint trained for a different task head.
#
#  LiteLLM / litellm
#    - "HTTP transport error on attempt N, retrying" — transient; LiteLLM
#      already retries automatically.
#    - "If you need to debug this error, use litellm._turn_on_debug()" —
#      informational noise on every retry.
#
#  httpx
#    - Low-level transport debug lines emitted when LiteLLM retries.
#
# Application loggers (django, researcher_ai_portal*) are left at their
# default WARNING level.  Set DJANGO_LOG_LEVEL=DEBUG in .env to get
# full debug output for the portal app.
# ---------------------------------------------------------------------------
_LOG_LEVEL = os.environ.get("DJANGO_LOG_LEVEL", "WARNING").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "loggers": {
        # Portal application — use DJANGO_LOG_LEVEL (default WARNING).
        "researcher_ai_portal": {
            "handlers": ["console"],
            "level": _LOG_LEVEL,
            "propagate": False,
        },
        "django": {
            "handlers": ["console"],
            "level": _LOG_LEVEL,
            "propagate": False,
        },
        # Suppress BertModel load-report noise from sentence-transformers.
        "sentence_transformers": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "transformers": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        # Suppress LiteLLM retry info messages.
        "LiteLLM": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "litellm": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        # Suppress low-level httpx transport lines emitted during LiteLLM retries.
        "httpx": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "httpcore": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
}

SESSION_COOKIE_SAMESITE = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax')
CSRF_COOKIE_SAMESITE = os.environ.get('CSRF_COOKIE_SAMESITE', 'Lax')

if not DEBUG:
    SECURE_SSL_REDIRECT = _env_bool('SECURE_SSL_REDIRECT', True)
    SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '31536000'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', True)
    SECURE_HSTS_PRELOAD = _env_bool('SECURE_HSTS_PRELOAD', True)
    SESSION_COOKIE_SECURE = _env_bool('SESSION_COOKIE_SECURE', True)
    CSRF_COOKIE_SECURE = _env_bool('CSRF_COOKIE_SECURE', True)
