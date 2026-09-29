"""Unit tests for core.ai_engine module.

Tests cover: AI availability check, LLM API invocation, experience file
reading, and document generation orchestration.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.ai_engine import (
    MAX_RETRIES,
    RETRY_BASE_DELAY,
    _call_anthropic,
    _call_google,
    _call_llm,
    _call_openai_compatible,
    check_ai_available,
    generate_documents,
    invoke_llm,
    read_all_experience_files,
    validate_api_key,
)

# ===================================================================
# FR-031 — AI Availability Check
# ===================================================================


class TestCheckAIAvailable:
    """FR-031: Detect whether an AI provider is configured."""

    # Validates AC-031-1
    def test_available_when_key_configured(self):
        """AC-031-1: Provider and API key set -> True."""
        llm = SimpleNamespace(provider="anthropic", api_key="sk-test-123")
        assert check_ai_available(llm) is True

    # Validates AC-031-2
    def test_unavailable_when_no_key(self):
        """AC-031-2: Empty API key -> False."""
        llm = SimpleNamespace(provider="anthropic", api_key="")
        assert check_ai_available(llm) is False

    def test_unavailable_when_no_provider(self):
        """No provider set -> False."""
        llm = SimpleNamespace(provider="", api_key="sk-test")
        assert check_ai_available(llm) is False

    # Validates AC-031-3
    def test_unavailable_when_none(self):
        """AC-031-3: None config -> False."""
        assert check_ai_available(None) is False


# ===================================================================
# FR-032 — LLM Invocation
# ===================================================================


class TestInvokeLLM:
    """FR-032: Invoke LLM API for text generation."""

    # Validates AC-032-1
    def test_returns_text_on_success(self):
        """AC-032-1: Successful API call returns text."""
        llm = SimpleNamespace(provider="anthropic", api_key="sk-test", model="")
        with patch("core.ai_engine._call_llm", return_value="Generated text"):
            result = invoke_llm("test prompt", llm)
            assert result == "Generated text"

    # Validates AC-032-2
    def test_raises_when_no_config(self):
        """AC-032-2: No config -> RuntimeError."""
        with pytest.raises(RuntimeError, match="No AI provider"):
            invoke_llm("test", None)

    # Validates AC-032-3
    def test_raises_when_no_api_key(self):
        """AC-032-3: Empty API key -> RuntimeError."""
        llm = SimpleNamespace(provider="anthropic", api_key="", model="")
        with pytest.raises(RuntimeError, match="No AI provider"):
            invoke_llm("test", llm)

    def test_uses_default_model_when_empty(self):
        """Uses provider default model when model field is empty."""
        llm = SimpleNamespace(provider="openai", api_key="sk-test", model="")
        with patch("core.ai_engine._call_llm", return_value="ok") as mock:
            invoke_llm("test", llm)
            mock.assert_called_once_with("openai", "sk-test", "gpt-6-luna", "test", 120)


# ===================================================================
# FR-032b — Provider-Specific API Calls
# ===================================================================


class TestAnthropicCall:
    """Test Anthropic API call."""

    def test_success(self):
        """Anthropic returns content[0].text."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "content": [{"text": "  Resume text  "}]
        }
        with patch("core.ai_engine.requests.post", return_value=mock_resp):
            result = _call_anthropic("sk-test", "claude-sonnet-4-20250514", "prompt", 30)
            assert result == "Resume text"

    def test_error(self):
        """Anthropic error raises RuntimeError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"error": {"message": "Invalid key"}}
        mock_resp.text = "Invalid key"
        with patch("core.ai_engine.requests.post", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="Anthropic"):
                _call_anthropic("bad-key", "model", "prompt", 30)


class TestOpenAICompatibleCall:
    """Test OpenAI-compatible API call (OpenAI and DeepSeek)."""

    def test_success(self):
        """OpenAI returns choices[0].message.content."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "  Cover letter  "}}]
        }
        with patch("core.ai_engine.requests.post", return_value=mock_resp):
            result = _call_openai_compatible("openai", "sk-test", "gpt-4o", "prompt", 30)
            assert result == "Cover letter"

    def test_deepseek(self):
        """DeepSeek uses same format as OpenAI."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "result"}}]
        }
        with patch("core.ai_engine.requests.post", return_value=mock_resp) as mock_post:
            _call_openai_compatible("deepseek", "sk-test", "deepseek-chat", "p", 30)
            call_url = mock_post.call_args[0][0]
            assert "deepseek.com" in call_url


class TestGoogleCall:
    """Test Google Gemini API call."""

    def test_success(self):
        """Google returns candidates[0].content.parts[0].text."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "  Generated  "}]}}]
        }
        with patch("core.ai_engine.requests.post", return_value=mock_resp):
            result = _call_google("key", "gemini-2.0-flash", "prompt", 30)
            assert result == "Generated"


class TestValidateAPIKey:
    """Test API key validation."""

    def test_valid_key(self):
        """Valid key returns True."""
        with patch("core.ai_engine._call_llm", return_value="OK"):
            assert validate_api_key("anthropic", "sk-valid") is True

    def test_invalid_key(self):
        """Invalid key returns False."""
        with patch("core.ai_engine._call_llm", side_effect=RuntimeError("401")):
            assert validate_api_key("anthropic", "bad-key") is False


# ===================================================================
# FR-033 — Read Experience Files
# ===================================================================


class TestReadAllExperienceFiles:
    """FR-033: Read and concatenate experience .txt files."""

    # Validates AC-033-1
    def test_reads_and_concatenates_files(self, tmp_path):
        """AC-033-1: Multiple files concatenated with section separators."""
        (tmp_path / "skills.txt").write_text("Python, Flask", encoding="utf-8")
        (tmp_path / "work_history.txt").write_text("5 years at Acme", encoding="utf-8")

        result = read_all_experience_files(tmp_path)
        assert "=== skills.txt ===" in result
        assert "Python, Flask" in result
        assert "=== work_history.txt ===" in result
        assert "5 years at Acme" in result

    # Validates AC-033-2
    def test_excludes_readme(self, tmp_path):
        """AC-033-2: README.txt is excluded."""
        (tmp_path / "README.txt").write_text("Instructions", encoding="utf-8")
        (tmp_path / "skills.txt").write_text("Python", encoding="utf-8")

        result = read_all_experience_files(tmp_path)
        assert "README.txt" not in result
        assert "Instructions" not in result
        assert "skills.txt" in result

    # Validates AC-033-3
    def test_empty_directory_returns_empty(self, tmp_path):
        """AC-033-3: No txt files -> empty string."""
        result = read_all_experience_files(tmp_path)
        assert result == ""

    def test_only_readme_returns_empty(self, tmp_path):
        """AC-033-3: Only README.txt -> empty string."""
        (tmp_path / "README.txt").write_text("Instructions", encoding="utf-8")
        result = read_all_experience_files(tmp_path)
        assert result == ""

    # Validates AC-033-N1
    def test_nonexistent_directory_returns_empty(self, tmp_path):
        """AC-033-N1: Non-existent directory -> empty string."""
        result = read_all_experience_files(tmp_path / "nonexistent")
        assert result == ""

    # Validates AC-033-N2
    def test_skips_unreadable_files(self, tmp_path):
        """AC-033-N2: Unreadable file skipped, others still read."""
        (tmp_path / "good.txt").write_text("Good content", encoding="utf-8")
        (tmp_path / "bad.txt").write_bytes(b"\x80\x81\x82")

        result = read_all_experience_files(tmp_path)
        assert "Good content" in result
        assert "bad.txt" not in result

    def test_files_sorted_alphabetically(self, tmp_path):
        """Files are sorted alphabetically by filename."""
        (tmp_path / "z_last.txt").write_text("Last", encoding="utf-8")
        (tmp_path / "a_first.txt").write_text("First", encoding="utf-8")

        result = read_all_experience_files(tmp_path)
        idx_first = result.index("a_first.txt")
        idx_last = result.index("z_last.txt")
        assert idx_first < idx_last


# ===================================================================
# FR-036 — Document Generation Orchestration
# ===================================================================


class TestGenerateDocuments:
    """FR-036: Full document generation flow."""

    @pytest.fixture()
    def mock_env(self, tmp_path):
        """Set up mock environment for generate_documents tests."""
        exp_dir = tmp_path / "experiences"
        exp_dir.mkdir()
        (exp_dir / "skills.txt").write_text("Python, Flask, AI", encoding="utf-8")

        res_dir = tmp_path / "resumes"
        cl_dir = tmp_path / "cover_letters"

        job = SimpleNamespace(
            id="test-uuid-123",
            raw=SimpleNamespace(
                company="Acme Corp",
                description="Senior Python developer needed",
            ),
        )

        profile = SimpleNamespace(
            full_name="John Doe",
            email="john@example.com",
            phone_full="+1555-1234",
            location="New York, NY",
            linkedin_url="https://linkedin.com/in/johndoe",
            portfolio_url=None,
            bio="Experienced engineer",
        )

        llm_config = SimpleNamespace(
            provider="anthropic", api_key="sk-test", model=""
        )

        call_count = {"n": 0}

        def mock_invoke(prompt, llm_cfg, timeout_seconds=120):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "# John Doe\njohn@example.com\n\n## Summary\nExperienced engineer."
            return "I bring 5 years of Python experience to this role."

        return {
            "job": job,
            "profile": profile,
            "exp_dir": exp_dir,
            "res_dir": res_dir,
            "cl_dir": cl_dir,
            "llm_config": llm_config,
            "call_count": call_count,
            "mock_invoke": mock_invoke,
        }

    # Validates AC-036-1
    def test_returns_existing_file_paths(self, mock_env):
        """AC-036-1: Returns tuple of paths that exist on disk."""
        with patch("core.ai_engine.invoke_llm", mock_env["mock_invoke"]):
            pdf_path, cl_path, _meta = generate_documents(
                mock_env["job"], mock_env["profile"],
                mock_env["exp_dir"], mock_env["res_dir"], mock_env["cl_dir"],
                llm_config=mock_env["llm_config"],
            )
        assert pdf_path.exists()
        assert cl_path.exists()

    # Validates AC-036-2
    def test_creates_three_files(self, mock_env):
        """AC-036-2: Creates .md, .pdf, and .txt files."""
        with patch("core.ai_engine.invoke_llm", mock_env["mock_invoke"]):
            pdf_path, cl_path, _meta = generate_documents(
                mock_env["job"], mock_env["profile"],
                mock_env["exp_dir"], mock_env["res_dir"], mock_env["cl_dir"],
                llm_config=mock_env["llm_config"],
            )
        assert pdf_path.suffix == ".pdf"
        assert cl_path.suffix == ".txt"
        md_path = pdf_path.with_suffix(".md")
        assert md_path.exists()

    # Validates AC-036-3
    def test_company_name_in_filename(self, mock_env):
        """AC-036-3: Company name has spaces replaced with hyphens, lowercased."""
        with patch("core.ai_engine.invoke_llm", mock_env["mock_invoke"]):
            pdf_path, _, _meta = generate_documents(
                mock_env["job"], mock_env["profile"],
                mock_env["exp_dir"], mock_env["res_dir"], mock_env["cl_dir"],
                llm_config=mock_env["llm_config"],
            )
        assert "acme-corp" in pdf_path.name

    def test_calls_llm_twice(self, mock_env):
        """LLM is invoked exactly twice (resume + cover letter)."""
        with patch("core.ai_engine.invoke_llm", mock_env["mock_invoke"]):
            generate_documents(
                mock_env["job"], mock_env["profile"],
                mock_env["exp_dir"], mock_env["res_dir"], mock_env["cl_dir"],
                llm_config=mock_env["llm_config"],
            )
        assert mock_env["call_count"]["n"] == 2

    # Validates AC-036-N1
    def test_raises_on_llm_failure(self, mock_env):
        """AC-036-N1: LLM failure raises RuntimeError."""
        def fail_invoke(prompt, llm_cfg, timeout_seconds=120):
            raise RuntimeError("API error")

        with patch("core.ai_engine.invoke_llm", fail_invoke):
            with pytest.raises(RuntimeError):
                generate_documents(
                    mock_env["job"], mock_env["profile"],
                    mock_env["exp_dir"], mock_env["res_dir"], mock_env["cl_dir"],
                    llm_config=mock_env["llm_config"],
                )

    def test_creates_output_directories(self, mock_env):
        """Output directories are created if they don't exist."""
        assert not mock_env["res_dir"].exists()
        assert not mock_env["cl_dir"].exists()

        with patch("core.ai_engine.invoke_llm", mock_env["mock_invoke"]):
            generate_documents(
                mock_env["job"], mock_env["profile"],
                mock_env["exp_dir"], mock_env["res_dir"], mock_env["cl_dir"],
                llm_config=mock_env["llm_config"],
            )
        assert mock_env["res_dir"].exists()
        assert mock_env["cl_dir"].exists()

    def test_cover_letter_content_saved(self, mock_env):
        """Cover letter text is correctly saved to .txt file."""
        with patch("core.ai_engine.invoke_llm", mock_env["mock_invoke"]):
            _, cl_path, _meta = generate_documents(
                mock_env["job"], mock_env["profile"],
                mock_env["exp_dir"], mock_env["res_dir"], mock_env["cl_dir"],
                llm_config=mock_env["llm_config"],
            )
        content = cl_path.read_text(encoding="utf-8")
        assert "5 years of Python experience" in content

    def test_resume_markdown_saved(self, mock_env):
        """Resume Markdown is saved to .md file."""
        with patch("core.ai_engine.invoke_llm", mock_env["mock_invoke"]):
            pdf_path, _, _meta = generate_documents(
                mock_env["job"], mock_env["profile"],
                mock_env["exp_dir"], mock_env["res_dir"], mock_env["cl_dir"],
                llm_config=mock_env["llm_config"],
            )
        md_path = pdf_path.with_suffix(".md")
        content = md_path.read_text(encoding="utf-8")
        assert "# John Doe" in content


# ===================================================================
# D-6 — LLM Retry with Exponential Backoff
# ===================================================================


class TestLLMRetry:
    """D-6: LLM API calls retry on transient errors with exponential backoff."""

    def test_retry_on_500_then_succeed(self):
        """D-6: Retries on 500 error and succeeds on second attempt.  # AC-D6.1"""
        mock_resp_500 = MagicMock()
        mock_resp_500.status_code = 500
        mock_resp_500.text = "Internal Server Error"
        mock_resp_500.json.return_value = {"error": {"message": "server error"}}

        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 200
        mock_resp_ok.json.return_value = {
            "content": [{"text": "success"}],
        }

        with patch("core.ai_engine.requests.post", side_effect=[mock_resp_500, mock_resp_ok]), \
             patch("core.ai_engine.time.sleep") as mock_sleep:
            result = _call_llm("anthropic", "key", "model", "test")
            assert result == "success"
            mock_sleep.assert_called_once_with(RETRY_BASE_DELAY)

    def test_retry_on_429_rate_limit(self):
        """D-6: Retries on 429 rate limit.  # AC-D6.2"""
        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        mock_resp_429.text = "Rate limited"
        mock_resp_429.json.return_value = {"error": {"message": "rate limited"}}

        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 200
        mock_resp_ok.json.return_value = {
            "content": [{"text": "ok"}],
        }

        with patch("core.ai_engine.requests.post", side_effect=[mock_resp_429, mock_resp_ok]), \
             patch("core.ai_engine.time.sleep"):
            result = _call_llm("anthropic", "key", "model", "test")
            assert result == "ok"

    def test_no_retry_on_401(self):
        """D-6: Does not retry on 401 auth error.  # AC-D6.3"""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_resp.json.return_value = {"error": {"message": "invalid key"}}

        with patch("core.ai_engine.requests.post", return_value=mock_resp), \
             patch("core.ai_engine.time.sleep") as mock_sleep:
            with pytest.raises(RuntimeError, match="401"):
                _call_llm("anthropic", "key", "model", "test")
            mock_sleep.assert_not_called()

    def test_no_retry_on_400(self):
        """D-6: Does not retry on 400 client error.  # AC-D6.4"""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"
        mock_resp.json.return_value = {"error": {"message": "bad request"}}

        with patch("core.ai_engine.requests.post", return_value=mock_resp), \
             patch("core.ai_engine.time.sleep") as mock_sleep:
            with pytest.raises(RuntimeError, match="400"):
                _call_llm("openai", "key", "model", "test")
            mock_sleep.assert_not_called()

    def test_retry_on_connection_error(self):
        """D-6: Retries on network connection error.  # AC-D6.5"""
        import requests as req

        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 200
        mock_resp_ok.json.return_value = {
            "content": [{"text": "recovered"}],
        }

        with patch("core.ai_engine.requests.post",
                    side_effect=[req.ConnectionError("network down"), mock_resp_ok]), \
             patch("core.ai_engine.time.sleep"):
            result = _call_llm("anthropic", "key", "model", "test")
            assert result == "recovered"

    def test_retry_on_timeout(self):
        """D-6: Retries on request timeout.  # AC-D6.6"""
        import requests as req

        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 200
        mock_resp_ok.json.return_value = {
            "choices": [{"message": {"content": "done"}}],
        }

        with patch("core.ai_engine.requests.post",
                    side_effect=[req.Timeout("timed out"), mock_resp_ok]), \
             patch("core.ai_engine.time.sleep"):
            result = _call_llm("openai", "key", "model", "test")
            assert result == "done"

    def test_exponential_backoff_delays(self):
        """D-6: Sleep delays double on each retry (1s, 2s, 4s).  # AC-D6.7"""
        mock_resp_500 = MagicMock()
        mock_resp_500.status_code = 500
        mock_resp_500.text = "error"
        mock_resp_500.json.return_value = {"error": {"message": "fail"}}

        with patch("core.ai_engine.requests.post", return_value=mock_resp_500), \
             patch("core.ai_engine.time.sleep") as mock_sleep:
            with pytest.raises(RuntimeError, match="after 4 attempts"):
                _call_llm("anthropic", "key", "model", "test")
            assert mock_sleep.call_count == MAX_RETRIES
            delays = [call.args[0] for call in mock_sleep.call_args_list]
            assert delays == [1.0, 2.0, 4.0]

    def test_unsupported_provider_no_retry(self):
        """D-6: Unsupported provider raises immediately without retry.  # AC-D6.8"""
        with patch("core.ai_engine.time.sleep") as mock_sleep:
            with pytest.raises(RuntimeError, match="Unsupported"):
                _call_llm("unknown", "key", "model", "test")
            mock_sleep.assert_not_called()

    def test_max_retries_constant(self):
        """D-6: MAX_RETRIES is 3.  # AC-D6.9"""
        assert MAX_RETRIES == 3
