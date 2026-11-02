"""`.env.example` is tracked by git: it must document every setting and hold no secret."""

import re

from app.core.config import ROOT_DIR, Settings

# Secrets, plus project-specific values that have no business in a template.
SENSITIVE_NAME = re.compile(
    r"PASSWORD|SECRET|API_KEY|ENCRYPTION_KEY|PUBLISHABLE_KEY|TOKEN|DATABASE_URL|CLIENT_ID"
    r"|SUPABASE_URL|SUPABASE_JWKS_URL"
)
URL_WITH_CREDENTIALS = re.compile(r"://[^/\s:@]+:[^/\s@]+@")


def _example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT_DIR / ".env.example").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if match:
            values[match[1]] = match[2].strip()
    return values


def test_example_file_holds_no_secret_values() -> None:
    offenders = [
        name
        for name, value in _example_values().items()
        if value and (SENSITIVE_NAME.search(name) or URL_WITH_CREDENTIALS.search(value))
    ]

    assert offenders == [], (
        f"{offenders} have values in .env.example, which is tracked by git. "
        "Real values belong in .env (gitignored)."
    )


def test_every_setting_is_documented() -> None:
    documented = _example_values()
    missing = [name.upper() for name in Settings.model_fields if name.upper() not in documented]

    assert missing == []


def test_real_env_files_are_gitignored() -> None:
    rules = (ROOT_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert ".env" in rules
    assert ".env.*" in rules
    assert "!.env.example" in rules
