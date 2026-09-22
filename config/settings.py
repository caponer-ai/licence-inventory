"""Project settings.

Secrets and the run mode come from the environment. The defaults are safe
for a local run: if a variable is forgotten in production, the app fails
loudly instead of quietly running with a development key.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env if it exists. Variables already present in the environment win
# (override=False): otherwise a file on disk would silently override what
# systemd or docker compose set, and the cause would take a while to find.
load_dotenv(BASE_DIR / ".env", override=False)

# The run mode is explicit. It, rather than DEBUG, decides whether the
# secret key may be omitted and whether hardening is switched on. The two
# are deliberately separate: DEBUG=False locally is a normal case (that is
# how tests run) and it should not demand production secrets.
ENV = os.environ.get("DJANGO_ENV", "local")
if ENV not in ("local", "production"):
    # A typo like "prod" used to fall through to local behaviour: a
    # development secret and no hardening, silently, in production.
    raise ImproperlyConfigured(f"DJANGO_ENV must be 'local' or 'production', got {ENV!r}")
IS_PRODUCTION = ENV == "production"

DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1" and not IS_PRODUCTION

# In production the key is mandatory: better to fail at startup than to run
# quietly with a key that sits in a public repository.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if IS_PRODUCTION:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required when DJANGO_ENV=production")
    SECRET_KEY = "local-development-only-3f8a1c9e7b2d4a6f5e0c8b1d9a7f3e2c6b4d8a0f"

ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    "inventory",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"

# SQLite by default so the project starts with no external dependencies.
# Set POSTGRES_DB and it goes to Postgres without a code change.
if os.environ.get("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["POSTGRES_DB"],
            "USER": os.environ.get("POSTGRES_USER", "postgres"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(BASE_DIR / "db.sqlite3"),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "inventory.exceptions.exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Closed by default. The open endpoints (healthz, the schema) opt in
    # explicitly rather than the other way round: forgetting to close is
    # easier than forgetting to open.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": os.environ.get("THROTTLE_ANON", "20/min"),
        "user": os.environ.get("THROTTLE_USER", "600/min"),
        # Login is stricter than everything else: it is the only place a
        # password is checked and the only one reachable without a token.
        "login": os.environ.get("THROTTLE_LOGIN", "5/min"),
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "licence-inventory API",
    "DESCRIPTION": "Inventory of units with a limited lifetime: issuing, renewal, warranty.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Locally the docs are open so they are visible right after cloning.
    # In production the schema is a map of your API, so it sits behind a token.
    "SERVE_PERMISSIONS": (
        ["rest_framework.permissions.IsAuthenticated"]
        if IS_PRODUCTION
        else ["rest_framework.permissions.AllowAny"]
    ),
}

# The throttle counters live here. The default local-memory cache is
# per-process, so with several gunicorn workers each one counts separately.
# Set a shared backend to make the limits global; see auth_views.py.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "licence-inventory",
    }
}

# Hardening is switched on exactly in production. Locally these settings
# would break http://localhost and the test run, hence the branch.
if IS_PRODUCTION:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    X_FRAME_OPTIONS = "DENY"
    # The container healthcheck talks plain HTTP to 127.0.0.1, so it must not
    # be bounced to HTTPS. Everything else still is.
    SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]
