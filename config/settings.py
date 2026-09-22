"""Налаштування проєкту.

Секрети й режим беремо з оточення. Дефолти безпечні для локального
запуску: якщо змінну забули на проді, падає DEBUG=False, а не навпаки.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Читаємо .env, якщо він є. Змінні, вже задані в оточенні, мають
# пріоритет (override=False): інакше файл на диску тихо перебивав би те,
# що задав systemd або docker compose, і причину шукали б довго.
load_dotenv(BASE_DIR / ".env", override=False)

# Режим задається явно. Саме він, а не DEBUG, керує тим, чи можна
# обійтись без секретного ключа і чи вмикати жорсткі налаштування.
# Розведено навмисно: DEBUG=False локально це нормальний випадок
# (так йдуть тести), і він не має вимагати продових секретів.
ENV = os.environ.get("DJANGO_ENV", "local")
IS_PRODUCTION = ENV == "production"

DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1" and not IS_PRODUCTION

# На проді ключ обов'язковий: краще впасти на старті, ніж тихо
# працювати з ключем, який лежить у публічному репозиторії.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if IS_PRODUCTION:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY обов'язковий при DJANGO_ENV=production")
    SECRET_KEY = "local-development-only-3f8a1c9e7b2d4a6f5e0c8b1d9a7f3e2c6b4d8a0f"

ALLOWED_HOSTS = [
    h
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h
]

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

# SQLite за замовчуванням, щоб проєкт піднімався однією командою.
# Задано POSTGRES_DB -> йдемо в Postgres без зміни коду.
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
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "uk"
TIME_ZONE = "Europe/Kyiv"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "inventory.exceptions.exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Закрито за замовчуванням. Відкриті точки (healthz, схема) вмикають
    # доступ явно, а не навпаки: забути закрити легше, ніж забути відкрити.
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
        # Логін жорсткіший за все інше: це єдине місце, де перевіряється
        # пароль, і єдине, куди можна стукати без токена.
        "login": os.environ.get("THROTTLE_LOGIN", "5/min"),
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "licence-inventory API",
    "DESCRIPTION": "Облік одиниць з обмеженим строком дії: видача, продовження, гарантія.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Локально документація відкрита, щоб її було видно одразу після
    # клонування. На проді схема це карта твого API, тому за токеном.
    "SERVE_PERMISSIONS": (
        ["rest_framework.permissions.IsAuthenticated"]
        if IS_PRODUCTION
        else ["rest_framework.permissions.AllowAny"]
    ),
}

# Жорсткі налаштування ввімкнені рівно на проді.
# Локально вони б ламали http://localhost і тести, тому розвилка.
if IS_PRODUCTION:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    X_FRAME_OPTIONS = "DENY"
