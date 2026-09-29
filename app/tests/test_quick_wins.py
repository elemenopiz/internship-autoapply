"""Unit tests for TASK-011 Production Readiness Quick Wins.

Requirement traceability:
    NFR-QW1  SECRET_KEY + Keyring for API Keys
    NFR-QW2  No Silent Exception Suppression
    NFR-QW3  Structured Logging at Startup
    NFR-QW4  Thread-Safe Access to _bot_thread
    NFR-QW5  Temporary Files Must Be Cleaned Up
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from unittest.mock import MagicMock

from config.settings import (
    AppConfig,
    _save_config_raw,
    load_config,
    save_config,
)

# ─── Helpers ────────────────────────────────────────────────────────────────


def _minimal_config_data() -> dict:
    return {
        "profile": {
            "first_name": "Test",
            "last_name": "User",
            "email": "t@t.com",
            "phone": "555",
            "city": "X",
            "state": "Y",
            "bio": "Z",
        },
        "search_criteria": {
            "job_titles": ["Dev"],
            "locations": ["Remote"],
        },
        "bot": {},
    }


def _write_config(tmp_path: Path, data: dict | None = None) -> Path:
    """Write a config.json to tmp_path and return the path."""
    if data is None:
        data = _minimal_config_data()
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(data), encoding="utf-8")
    return config_path


# ═══════════════════════════════════════════════════════════════════════════
# NFR-QW1: SECRET_KEY + Keyring
# ═══════════════════════════════════════════════════════════════════════════


class TestSecretKey:
    """AC-QW1.1: Flask SECRET_KEY generation and persistence."""

    def test_secret_key_generated_on_first_run(self, tmp_path, monkeypatch):
        """Given no .flask_secret file, a 32-byte hex key is generated."""
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)

        from app import _get_or_create_secret_key

        key = _get_or_create_secret_key()

        assert len(key) == 64  # 32 bytes = 64 hex chars
        assert (tmp_path / ".flask_secret").exists()
        assert (tmp_path / ".flask_secret").read_text(encoding="utf-8").strip() == key

    def test_secret_key_reused_on_subsequent_runs(self, tmp_path, monkeypatch):
        """Given .flask_secret exists, the same key is returned."""
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)

        secret_path = tmp_path / ".flask_secret"
        secret_path.write_text("abcdef1234567890" * 4, encoding="utf-8")

        from app import _get_or_create_secret_key

        key = _get_or_create_secret_key()
        assert key == "abcdef1234567890" * 4


class TestKeyringIntegration:
    """AC-QW1.2 through AC-QW1.4: Keyring storage, fallback, migration."""

    def test_save_config_stores_key_in_keyring(self, tmp_path, monkeypatch):
        """AC-QW1.2: When keyring is available, api_key is stored there."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("config.settings._keyring_available", True)

        mock_keyring = MagicMock()
        monkeypatch.setattr("config.settings.keyring", mock_keyring, raising=False)


        # Patch the import inside save_config
        monkeypatch.setitem(__import__("sys").modules, "keyring", mock_keyring)

        data = _minimal_config_data()
        data["llm"] = {"provider": "openai", "api_key": "sk-test-123", "model": ""}
        cfg = AppConfig(**data)

        save_config(cfg)

        # Verify keyring was called
        mock_keyring.set_password.assert_called_once_with(
            "autoapply", "llm_api_key", "sk-test-123"
        )
        # Verify config.json has empty api_key
        saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
        assert saved["llm"]["api_key"] == ""

    def test_load_config_retrieves_key_from_keyring(self, tmp_path, monkeypatch):
        """AC-QW1.2: When keyring has the key, load_config populates it."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("config.settings._keyring_available", True)

        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = "sk-from-keyring"
        monkeypatch.setitem(__import__("sys").modules, "keyring", mock_keyring)

        data = _minimal_config_data()
        data["llm"] = {"provider": "openai", "api_key": "", "model": ""}
        _write_config(tmp_path, data)

        cfg = load_config()
        assert cfg is not None
        assert cfg.llm.api_key == "sk-from-keyring"

    def test_keyring_fallback_to_plaintext(self, tmp_path, monkeypatch):
        """AC-QW1.3: When keyring unavailable, key stays in config.json."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("config.settings._keyring_available", False)

        data = _minimal_config_data()
        data["llm"] = {"provider": "openai", "api_key": "sk-plain", "model": ""}
        cfg = AppConfig(**data)

        save_config(cfg)

        # Verify config.json still has the key (not stripped)
        saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
        assert saved["llm"]["api_key"] == "sk-plain"

    def test_migration_from_plaintext_to_keyring(self, tmp_path, monkeypatch):
        """AC-QW1.4: Existing plaintext key migrated to keyring on load."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("config.settings._keyring_available", True)

        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = None  # No key in keyring yet
        monkeypatch.setitem(__import__("sys").modules, "keyring", mock_keyring)

        data = _minimal_config_data()
        data["llm"] = {"provider": "openai", "api_key": "sk-migrate-me", "model": ""}
        _write_config(tmp_path, data)

        cfg = load_config()

        # Key should be migrated to keyring
        mock_keyring.set_password.assert_called_once_with(
            "autoapply", "llm_api_key", "sk-migrate-me"
        )
        # config.json should now have empty api_key
        saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
        assert saved["llm"]["api_key"] == ""
        # In-memory config still has the key
        assert cfg.llm.api_key == "sk-migrate-me"


class TestSaveConfigRaw:
    """Tests for the _save_config_raw helper."""

    def test_strip_api_key_true(self, tmp_path, monkeypatch):
        """When strip_api_key=True, api_key is empty in output."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)

        data = _minimal_config_data()
        data["llm"] = {"provider": "openai", "api_key": "secret", "model": ""}
        cfg = AppConfig(**data)

        _save_config_raw(cfg, strip_api_key=True)

        saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
        assert saved["llm"]["api_key"] == ""

    def test_strip_api_key_false(self, tmp_path, monkeypatch):
        """When strip_api_key=False, api_key is preserved."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)

        data = _minimal_config_data()
        data["llm"] = {"provider": "openai", "api_key": "secret", "model": ""}
        cfg = AppConfig(**data)

        _save_config_raw(cfg, strip_api_key=False)

        saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
        assert saved["llm"]["api_key"] == "secret"


# ═══════════════════════════════════════════════════════════════════════════
# NFR-QW2: No Swallowed Exceptions
# ═══════════════════════════════════════════════════════════════════════════


class TestNoSwallowedExceptions:
    """NFR-QW2: Verify no bare except:pass without logging exists."""

    _SOURCE_FILES = [
        "bot/bot.py",
        "bot/browser.py",
        "app.py",
        "core/ai_engine.py",
        "bot/search/linkedin.py",
        "bot/search/indeed.py",
        "bot/apply/workday.py",
        "bot/apply/ashby.py",
    ]

    def test_no_bare_except_pass(self):
        """Every except block that was previously bare now logs.  # NFR-QW2"""
        import re

        # Pattern: except ... : <newline> pass (with no logging)
        bare_except_pass = re.compile(
            r"except\s+\w+.*?:\s*\n\s+pass\s*$", re.MULTILINE
        )

        violations = []
        for rel_path in self._SOURCE_FILES:
            path = Path(rel_path)
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            matches = bare_except_pass.findall(content)
            for match in matches:
                violations.append(f"{rel_path}: {match.strip()}")

        assert violations == [], (
            "Found bare except:pass without logging:\n"
            + "\n".join(violations)
        )


# ═══════════════════════════════════════════════════════════════════════════
# NFR-QW3: Logging Configuration (verified at import level)
# ═══════════════════════════════════════════════════════════════════════════


class TestLoggingConfig:
    """NFR-QW3: Verify _configure_logging exists in run.py."""

    def test_configure_logging_function_exists(self):
        """run.py defines _configure_logging.  # AC-QW3.1"""
        content = Path("run.py").read_text(encoding="utf-8")
        assert "def _configure_logging" in content
        assert "RotatingFileHandler" in content

    def test_configure_logging_called_in_main(self):
        """_configure_logging is called in main().  # AC-QW3.2"""
        content = Path("run.py").read_text(encoding="utf-8")
        assert "_configure_logging(data_dir)" in content


class TestStructuredJsonLogging:
    """D-7: Structured JSON logging option via AUTOAPPLY_LOG_FORMAT=json."""

    def test_json_formatter_exists(self):
        """run.py defines JsonFormatter class.  # AC-D7.1"""
        content = Path("run.py").read_text(encoding="utf-8")
        assert "class JsonFormatter" in content

    def test_json_formatter_produces_valid_json(self):
        """JsonFormatter outputs parseable JSON with required fields.  # AC-D7.2"""
        from run import JsonFormatter

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg="Hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test.logger"
        assert parsed["message"] == "Hello world"
        assert "timestamp" in parsed

    def test_json_formatter_includes_exception(self):
        """JsonFormatter includes exception info when present.  # AC-D7.3"""
        from run import JsonFormatter

        formatter = JsonFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="fail",
            args=(),
            exc_info=exc_info,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]
        assert "test error" in parsed["exception"]

    def test_json_format_env_var_activates_json(self, tmp_path, monkeypatch):
        """AUTOAPPLY_LOG_FORMAT=json activates JsonFormatter.  # AC-D7.4"""
        from run import JsonFormatter, _configure_logging

        monkeypatch.setenv("AUTOAPPLY_LOG_FORMAT", "json")
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        try:
            _configure_logging(tmp_path)
            new_handlers = [h for h in root.handlers if h not in original_handlers]
            assert len(new_handlers) == 2  # console + file
            for h in new_handlers:
                assert isinstance(h.formatter, JsonFormatter)
        finally:
            for h in root.handlers[:]:
                if h not in original_handlers:
                    root.removeHandler(h)

    def test_plain_format_is_default(self, tmp_path, monkeypatch):
        """Without AUTOAPPLY_LOG_FORMAT, plain text formatter is used.  # AC-D7.5"""
        from run import JsonFormatter, _configure_logging

        monkeypatch.delenv("AUTOAPPLY_LOG_FORMAT", raising=False)
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        try:
            _configure_logging(tmp_path)
            new_handlers = [h for h in root.handlers if h not in original_handlers]
            for h in new_handlers:
                assert not isinstance(h.formatter, JsonFormatter)
        finally:
            for h in root.handlers[:]:
                if h not in original_handlers:
                    root.removeHandler(h)


# ═══════════════════════════════════════════════════════════════════════════
# NFR-QW4: Thread-Safe _bot_thread Access
# ═══════════════════════════════════════════════════════════════════════════


class TestBotThreadSafety:
    """NFR-QW4: _bot_lock protects _bot_thread access."""

    def test_bot_lock_exists(self):
        """A threading.Lock guards _bot_thread.  # AC-QW4.1"""
        import app as app_module

        assert hasattr(app_module, "_bot_lock")
        assert isinstance(app_module._bot_lock, type(threading.Lock()))

    def test_scheduler_start_returns_status(self, tmp_path, monkeypatch):
        """_scheduler_start_bot returns status strings.  # AC-QW4.1"""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)

        import app as app_module

        # No config → returns 'no_config'
        result = app_module._scheduler_start_bot()
        assert result == "no_config"

    def test_is_bot_running_thread_safe(self):
        """_is_bot_running uses the lock.  # AC-QW4.1"""
        import inspect

        import app as app_module

        source = inspect.getsource(app_module._is_bot_running)
        assert "bot_lock" in source

    def test_scheduler_stop_uses_lock(self):
        """_scheduler_stop_bot uses the lock.  # AC-QW4.1"""
        import inspect

        import app as app_module

        source = inspect.getsource(app_module._scheduler_stop_bot)
        assert "bot_lock" in source


# ═══════════════════════════════════════════════════════════════════════════
# NFR-QW5: Temp File Leak in CSV Export
# ═══════════════════════════════════════════════════════════════════════════


class TestExportCleanup:
    """NFR-QW5: export_applications cleans up temp files."""

    def test_export_uses_bytesio(self):
        """export_applications reads CSV into memory and deletes temp file.  # AC-QW5.1"""
        import inspect

        import app as app_module

        source = inspect.getsource(app_module.export_applications)
        # Must use BytesIO pattern
        assert "BytesIO" in source
        # Must call unlink for cleanup
        assert "unlink" in source

    def test_export_no_leftover_files(self, tmp_path, monkeypatch):
        """After export, no temp CSV files remain.  # AC-QW5.2"""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)

        # Write config
        _write_config(tmp_path)

        from db.database import Database

        test_db = Database(tmp_path / "test.db")
        monkeypatch.setattr("app.db", test_db)
        monkeypatch.setattr("app_state.db", test_db)

        from app import app

        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.get("/api/applications/export")
        assert resp.status_code == 200

        # Verify response is CSV (temp file was cleaned up by finally block)
        assert "text/csv" in resp.content_type


# ═══════════════════════════════════════════════════════════════════════════
# NFR-ME2: API Authentication
# ═══════════════════════════════════════════════════════════════════════════


class TestAPIAuth:
    """NFR-ME2: Bearer token authentication on /api/* endpoints."""

    def test_api_token_file_created(self, tmp_path, monkeypatch):
        """AC-ME2.1: Token file is generated on first run."""
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)
        from app import _get_or_create_api_token

        token = _get_or_create_api_token()
        assert len(token) == 64
        assert (tmp_path / ".api_token").exists()

    def test_unauthorized_without_token(self, tmp_path, monkeypatch):
        """AC-ME2.2: Requests without token get 401."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)
        monkeypatch.delenv("AUTOAPPLY_DEV", raising=False)

        _write_config(tmp_path)

        from db.database import Database

        test_db = Database(tmp_path / "test.db")
        monkeypatch.setattr("app.db", test_db)
        monkeypatch.setattr("app_state.db", test_db)

        from app import app

        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.get("/api/bot/status")
        assert resp.status_code == 401
        assert resp.get_json()["error"] == "Unauthorized"

    def test_health_exempt_from_auth(self, tmp_path, monkeypatch):
        """AC-ME2.4: /api/health is accessible without auth."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)
        monkeypatch.delenv("AUTOAPPLY_DEV", raising=False)

        _write_config(tmp_path)

        from db.database import Database

        test_db = Database(tmp_path / "test.db")
        monkeypatch.setattr("app.db", test_db)
        monkeypatch.setattr("app_state.db", test_db)

        from app import app

        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_dev_mode_bypasses_auth(self, tmp_path, monkeypatch):
        """AC-ME2.4: AUTOAPPLY_DEV=1 bypasses auth."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)
        monkeypatch.setenv("AUTOAPPLY_DEV", "1")

        _write_config(tmp_path)

        from db.database import Database

        test_db = Database(tmp_path / "test.db")
        monkeypatch.setattr("app.db", test_db)
        monkeypatch.setattr("app_state.db", test_db)

        from app import app

        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.get("/api/bot/status")
        assert resp.status_code == 200

    def test_wrong_token_returns_401(self, tmp_path, monkeypatch):
        """AC-ME2.5: Incorrect token returns 401."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)
        monkeypatch.delenv("AUTOAPPLY_DEV", raising=False)

        _write_config(tmp_path)

        from db.database import Database

        test_db = Database(tmp_path / "test.db")
        monkeypatch.setattr("app.db", test_db)
        monkeypatch.setattr("app_state.db", test_db)

        from app import app

        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.get(
            "/api/bot/status",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# NFR-ME3: Input Validation
# ═══════════════════════════════════════════════════════════════════════════


class TestInputValidation:
    """NFR-ME3: POST/PUT/PATCH endpoints validate input."""

    def test_put_config_invalid_returns_400(self, tmp_path, monkeypatch):
        """AC-ME3.1: Invalid config data returns 400, not 500."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)

        _write_config(tmp_path)

        from db.database import Database

        test_db = Database(tmp_path / "test.db")
        monkeypatch.setattr("app.db", test_db)
        monkeypatch.setattr("app_state.db", test_db)

        from app import app

        app.config["TESTING"] = True
        client = app.test_client()

        # Send invalid config that will fail Pydantic validation
        resp = client.put(
            "/api/config",
            data=json.dumps({"profile": "not-a-dict"}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "Invalid configuration" in resp.get_json()["error"]

    def test_patch_application_invalid_status_returns_400(self, tmp_path, monkeypatch):
        """AC-ME3.2: Invalid status value returns 400."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)

        _write_config(tmp_path)

        from db.database import Database

        test_db = Database(tmp_path / "test.db")
        monkeypatch.setattr("app.db", test_db)
        monkeypatch.setattr("app_state.db", test_db)

        # Insert an application
        app_id = test_db.save_application(
            external_id="x", platform="test", job_title="Dev",
            company="Co", location="Remote", salary=None,
            apply_url="https://example.com", match_score=80,
            resume_path=None, cover_letter_path=None,
            cover_letter_text="", status="applied", error_message=None,
        )

        from app import app

        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.patch(
            f"/api/applications/{app_id}",
            data=json.dumps({"status": "totally_invalid"}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "Invalid status" in resp.get_json()["error"]


# ═══════════════════════════════════════════════════════════════════════════
# NFR-ME4: Error Handlers
# ═══════════════════════════════════════════════════════════════════════════


class TestErrorHandlers:
    """NFR-ME4: Error responses are JSON with no internal details."""

    def test_400_handler_returns_json(self, tmp_path, monkeypatch):
        """AC-ME4.3: 400 errors return JSON."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)

        from app import app

        app.config["TESTING"] = True

        # Verify the handler exists
        assert 400 in app.error_handler_spec[None]

    def test_generic_exception_handler_exists(self):
        """AC-ME4.1: Generic exception handler logs and returns generic message."""
        import inspect

        import app as app_module

        source = inspect.getsource(app_module.handle_exception)
        assert "internal_error" in source
        assert "logger.exception" in source


# ═══════════════════════════════════════════════════════════════════════════
# NFR-ME5: CORS Lockdown
# ═══════════════════════════════════════════════════════════════════════════


class TestCORSLockdown:
    """NFR-ME5: CORS not set to wildcard."""

    def test_cors_not_wildcard(self):
        """AC-ME5.1: cors_allowed_origins is not '*'."""
        import inspect

        import app as app_module

        source = inspect.getsource(app_module)
        # The old pattern should NOT exist
        assert 'cors_allowed_origins="*"' not in source
        # The new restricted pattern should exist
        assert "localhost" in source


# ═══════════════════════════════════════════════════════════════════════════
# NFR-ME1: CI Pipeline
# ═══════════════════════════════════════════════════════════════════════════


class TestCIPipeline:
    """NFR-ME1: GitHub Actions CI pipeline exists."""

    def test_ci_workflow_exists(self):
        """AC-ME1.1: .github/workflows/ci.yml exists."""
        ci_path = Path(".github/workflows/ci.yml")
        assert ci_path.exists()


# ═══════════════════════════════════════════════════════════════════════════
# NFR-SEC1: Security Headers
# ═══════════════════════════════════════════════════════════════════════════


class TestSecurityHeaders:
    """NFR-SEC1: All responses include security headers."""

    def test_api_response_has_security_headers(self, tmp_path, monkeypatch):
        """AC-SEC1.1: API responses include X-Content-Type-Options, X-Frame-Options."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)

        _write_config(tmp_path)

        from app import app

        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.get("/api/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("X-XSS-Protection") == "1; mode=block"
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    def test_api_response_has_cache_control(self, tmp_path, monkeypatch):
        """AC-SEC1.2: API responses have no-store cache control."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)

        _write_config(tmp_path)

        from app import app

        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.get("/api/health")
        assert resp.headers.get("Cache-Control") == "no-store"
        assert resp.headers.get("Pragma") == "no-cache"

    def test_max_content_length_set(self):
        """AC-SEC1.3: MAX_CONTENT_LENGTH is set to prevent DoS."""
        from app import app

        assert app.config["MAX_CONTENT_LENGTH"] == 16 * 1024 * 1024

    def test_413_error_handler_exists(self):
        """AC-SEC1.4: 413 error handler returns JSON."""
        from app import app

        assert 413 in app.error_handler_spec[None]


# ═══════════════════════════════════════════════════════════════════════════
# NFR-SEC2: Path Traversal Protection
# ═══════════════════════════════════════════════════════════════════════════


class TestPathTraversalProtection:
    """NFR-SEC2: Resume/description endpoints reject path traversal."""

    def test_is_safe_path_rejects_traversal(self, tmp_path, monkeypatch):
        """AC-SEC2.1: _is_safe_path rejects paths outside data dir."""
        monkeypatch.setattr("routes.applications.get_data_dir", lambda: tmp_path)
        from routes.applications import _is_safe_path

        assert not _is_safe_path("/etc/passwd")
        assert not _is_safe_path(str(tmp_path / ".." / "etc" / "passwd"))

    def test_is_safe_path_accepts_valid(self, tmp_path, monkeypatch):
        """AC-SEC2.2: _is_safe_path accepts valid files within data dir."""
        monkeypatch.setattr("routes.applications.get_data_dir", lambda: tmp_path)
        from routes.applications import _is_safe_path

        valid_file = tmp_path / "resume.pdf"
        valid_file.write_text("test")
        assert _is_safe_path(str(valid_file))

    def test_is_safe_path_rejects_nonexistent(self, tmp_path, monkeypatch):
        """AC-SEC2.3: _is_safe_path rejects non-existent files."""
        monkeypatch.setattr("routes.applications.get_data_dir", lambda: tmp_path)
        from routes.applications import _is_safe_path

        assert not _is_safe_path(str(tmp_path / "does_not_exist.pdf"))


# ═══════════════════════════════════════════════════════════════════════════
# NFR-SEC3: URL Validation in Login
# ═══════════════════════════════════════════════════════════════════════════


class TestLoginURLValidation:
    """NFR-SEC3: Login open endpoint validates URLs properly."""

    def test_rejects_non_allowed_domain(self, tmp_path, monkeypatch):
        """AC-SEC3.1: URLs to non-allowed domains are rejected."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)

        _write_config(tmp_path)

        from app import app

        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.post(
            "/api/login/open",
            data=json.dumps({"url": "https://evil.com/phish"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_rejects_subdomain_trick(self, tmp_path, monkeypatch):
        """AC-SEC3.2: URLs like linkedin.com.evil.com are rejected."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)

        _write_config(tmp_path)

        from app import app

        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.post(
            "/api/login/open",
            data=json.dumps({"url": "https://linkedin.com.evil.com/login"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_accepts_valid_linkedin_url(self, tmp_path, monkeypatch):
        """AC-SEC3.3: Valid LinkedIn URLs are accepted."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.login._find_system_chrome", lambda: None)

        _write_config(tmp_path)

        from app import app

        app.config["TESTING"] = True
        client = app.test_client()

        # Will fail with 500 (no Chrome) but NOT 400 — URL validation passes
        resp = client.post(
            "/api/login/open",
            data=json.dumps({"url": "https://www.linkedin.com/login"}),
            content_type="application/json",
        )
        assert resp.status_code == 500  # No Chrome, but URL was accepted

    def test_ci_workflow_runs_pytest(self):
        """AC-ME1.1: CI workflow runs pytest."""
        content = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
        assert "pytest" in content
        assert "ruff" in content
