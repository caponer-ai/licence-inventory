"""Settings deserve tests too.

These catch a class of mistake that otherwise only shows up in production:
the app came up, but with a default secret or without HTTPS.

They run in a separate process on purpose: settings are read once at import
time, so the environment cannot be swapped inside a running process without
dirty module reloads.
"""

import secrets
import subprocess
import sys
from pathlib import Path

import pytest

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


@pytest.mark.slow
def test_production_without_secret_key_refuses_to_start():
    result = run_manage(["check"], {"DJANGO_ENV": "production", "DJANGO_SECRET_KEY": ""})
    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stdout + result.stderr


@pytest.mark.slow
def test_production_with_secret_key_passes_deploy_checklist():
    """`check --deploy` has to be clean, not "six warnings, we know".

    ``--fail-level WARNING`` is essential: without it ``check`` exits zero
    even with six warnings and the test would be decoration.

    The key is generated rather than "x" * 60, because Django complains
    separately about a key with fewer than five distinct characters (W009).
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


@pytest.mark.slow
def test_local_run_needs_no_secrets():
    """The README promises a one-command start. Check that it is not a lie."""
    result = run_manage(["check"], {"DJANGO_ENV": "local", "DJANGO_SECRET_KEY": ""})
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.slow
def test_unknown_env_refuses_to_start():
    """A typo used to fall through to local: dev secret, no hardening."""
    result = run_manage(["check"], {"DJANGO_ENV": "prod"})
    assert result.returncode != 0
    assert "DJANGO_ENV" in result.stdout + result.stderr


@pytest.mark.slow
def test_seed_demo_refuses_to_run_in_production():
    """The command creates a staff user whose password is in the README."""
    result = run_manage(
        ["seed_demo"],
        {"DJANGO_ENV": "production", "DJANGO_SECRET_KEY": secrets.token_urlsafe(50)},
    )
    assert result.returncode != 0
    assert "production" in (result.stdout + result.stderr).lower()
