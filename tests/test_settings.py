"""Налаштування теж мають бути перевірені.

Ці два тести ловлять клас помилок, який інакше виявляється тільки на
проді: проєкт піднявся, але з дефолтним секретом або без HTTPS.

Запускаємо окремим процесом навмисно: налаштування читаються один раз при
імпорті, тому підмінити оточення всередині вже запущеного процесу не можна
без брудних перезавантажень модуля.
"""

import secrets
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def run_manage(args: list[str], env_extra: dict[str, str]):
    import os

    env = {**os.environ, **env_extra}
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_production_without_secret_key_refuses_to_start():
    result = run_manage(["check"], {"DJANGO_ENV": "production", "DJANGO_SECRET_KEY": ""})
    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stdout + result.stderr


def test_production_with_secret_key_passes_deploy_checklist():
    """`check --deploy` має бути чистим, а не «шість попереджень, ми знаємо».

    ``--fail-level WARNING`` обов'язковий: без нього ``check`` віддає нуль
    навіть із шістьма попередженнями, і тест був би декорацією.

    Ключ генеруємо, а не беремо "x" * 60: Django окремо лається на
    ключ із менш ніж п’ятьма різними символами (W009).
    """
    result = run_manage(
        ["check", "--deploy", "--fail-level", "WARNING"],
        {
            "DJANGO_ENV": "production",
            "DJANGO_SECRET_KEY": secrets.token_urlsafe(50),
            "DJANGO_DEBUG": "0",
        },
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "no issues" in output.lower(), output


def test_local_run_needs_no_secrets():
    """README обіцяє запуск однією командою. Перевіряємо, що не збрехали."""
    result = run_manage(["check"], {"DJANGO_ENV": "local", "DJANGO_SECRET_KEY": ""})
    assert result.returncode == 0, result.stdout + result.stderr
