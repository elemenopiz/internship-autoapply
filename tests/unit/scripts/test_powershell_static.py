"""Static lint for the PowerShell launchers (PowerShell is not available in the Linux build sandbox)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ["set_openai_key.ps1", "check_ready.ps1", "launch.ps1", "scripts/_common.ps1"]


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_exists_is_ascii_and_has_no_secrets(name: str) -> None:
    path = ROOT / name
    assert path.is_file(), f"{name} is required by the README"
    text = path.read_text(encoding="utf-8")
    assert text.isascii(), (
        "keep scripts ASCII-only so Windows PowerShell 5.1 parses them regardless of BOM"
    )
    assert not re.search(r"sk-[A-Za-z0-9]{16,}", text), "no hard-coded API keys"
    assert "Invoke-Expression" not in text and not re.search(r"\biex\b", text)


@pytest.mark.parametrize("name", ["set_openai_key.ps1", "check_ready.ps1", "launch.ps1"])
def test_entry_scripts_fail_fast(name: str) -> None:
    assert "$ErrorActionPreference = 'Stop'" in (ROOT / name).read_text(encoding="utf-8")


def test_set_key_script_never_echoes_or_persists_the_key() -> None:
    text = (ROOT / "set_openai_key.ps1").read_text(encoding="utf-8")
    assert "-AsSecureString" in text
    assert "SetEnvironmentVariable('OPENAI_API_KEY', $plain, 'User')" in text
    assert "ZeroFreeBSTR" in text
    for forbidden in (
        r"Write-Host[^\n]*\$plain",
        r"Write-Output[^\n]*\$plain",
        r"Out-File",
        r"Set-Content",
        r"Add-Content",
    ):
        assert not re.search(forbidden, text), f"key must never be printed or written: {forbidden}"
    assert "config.json" not in text.split("#>", 1)[1], "the script must not touch config.json"


def test_launcher_uses_documented_cli_commands() -> None:
    launch = (ROOT / "launch.ps1").read_text(encoding="utf-8")
    check = (ROOT / "check_ready.ps1").read_text(encoding="utf-8")
    assert "autoapply check-ready" in launch and "autoapply check-ready" in check
    assert "'serve'" in launch and "127.0.0.1" in launch, "dashboard must bind to loopback only"
    assert "Import-UserEnvVar" in launch and "Import-UserEnvVar" in check
    common = (ROOT / "scripts/_common.ps1").read_text(encoding="utf-8")
    assert "playwright install chromium" in common
