"""Host allow-list, CSRF, cross-site rejection, security headers, body limits."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from autoapply.dashboard.app import create_app
from autoapply.dashboard.security import (
    CONTENT_SECURITY_POLICY,
    SessionSigner,
    host_allowed,
    is_loopback_bind_host,
    split_host_header,
)

from .conftest import BASE_URL, csrf_from_html

EXPECTED_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def assert_secure_headers(response: Any) -> None:
    h = response.headers
    assert h["content-security-policy"] == EXPECTED_CSP
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    assert h["cache-control"] == "no-store"
    assert h["cross-origin-resource-policy"] == "same-origin"


def test_csp_constant_matches_the_spec() -> None:
    assert CONTENT_SECURITY_POLICY == EXPECTED_CSP


# ------------------------------------------------------------------------------------------ host


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "127.0.0.1:8765", "localhost", "LOCALHOST:9", "[::1]", "[::1]:8765"],
)
def test_loopback_hosts_are_served(bare_client: TestClient, host: str) -> None:
    assert bare_client.get("/healthz", headers={"host": host}).status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "evil.example:8765",
        "127.0.0.1.evil.example",
        "localhost.evil.example",
        "foo.localhost",
        "0.0.0.0:8765",
        "127.0.0.2",
        "[::2]",
        "localhost:99999",
        "127.0.0.1@evil.example",
        "localhost, evil.example",
        "",
    ],
)
def test_other_hosts_are_refused_with_400(bare_client: TestClient, host: str) -> None:
    for path in ("/healthz", "/", "/api/state", "/static/app.js"):
        response = bare_client.get(path, headers={"host": host})
        assert response.status_code == 400, (path, host)
        assert response.json()["code"] == "bad_host"
        assert_secure_headers(response)
    assert "set-cookie" not in response.headers


def test_extra_allowed_hosts_are_opt_in(runtime: Any) -> None:
    app = create_app(runtime, allowed_hosts=["dash.lan"])
    with TestClient(app, base_url="http://dash.lan:8765") as client:
        assert client.get("/healthz").status_code == 200
    with TestClient(app, base_url="http://other.lan:8765") as client:
        assert client.get("/healthz").status_code == 400


def test_host_helpers() -> None:
    assert split_host_header("[::1]:80") == ("[::1]", 80)
    assert split_host_header("a b") is None
    assert not host_allowed(None)
    assert is_loopback_bind_host("127.0.0.1") and is_loopback_bind_host("::1")
    assert is_loopback_bind_host("localhost") and is_loopback_bind_host("127.0.0.5")
    assert not is_loopback_bind_host("0.0.0.0")
    assert not is_loopback_bind_host("192.168.1.5")
    assert not is_loopback_bind_host("example.com")


# ------------------------------------------------------------------------------------------ headers


def test_security_headers_on_every_kind_of_response(bare_client: TestClient, runtime: Any) -> None:
    responses = [
        bare_client.get("/"),
        bare_client.get("/api/state"),
        bare_client.get("/static/app.css"),
        bare_client.get("/static/app.js"),
        bare_client.get("/nope"),
        bare_client.get("/api/nope"),
        bare_client.post("/api/stop"),  # 403 from the CSRF check
        bare_client.get("/api/opportunities?limit=0"),  # 422
        bare_client.get("/healthz", headers={"host": "evil.example"}),
    ]
    assert [r.status_code for r in responses] == [200, 200, 200, 200, 404, 404, 403, 422, 400]
    for response in responses:
        assert_secure_headers(response)


def test_unhandled_errors_become_a_generic_500_with_headers(
    bare_client: TestClient, runtime: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(limit: int | None = 50) -> Any:
        raise RuntimeError("secret internal detail /home/user/x")

    monkeypatch.setattr(runtime.repo, "list_runs", explode)
    response = bare_client.get("/api/runs")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "secret internal detail" not in response.text
    assert_secure_headers(response)


def test_docs_and_openapi_are_disabled(bare_client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert bare_client.get(path).status_code == 404


# ------------------------------------------------------------------------------------------ session


def test_session_cookie_flags_and_meta_token(bare_client: TestClient) -> None:
    response = bare_client.get("/")
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("autoapply_session=")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    token = csrf_from_html(response.text)
    assert token == bare_client.get("/api/csrf").json()["csrf_token"]
    assert "set-cookie" not in bare_client.get("/").headers  # cookie already held


def test_static_files_do_not_mint_sessions(bare_client: TestClient) -> None:
    assert "set-cookie" not in bare_client.get("/static/app.css").headers


def test_tokens_are_per_session(app: Any) -> None:
    with TestClient(app, base_url=BASE_URL) as a, TestClient(app, base_url=BASE_URL) as b:
        assert a.get("/api/csrf").json()["csrf_token"] != b.get("/api/csrf").json()["csrf_token"]


def test_signer_verifies_only_its_own_tokens() -> None:
    signer, other = SessionSigner(b"k" * 32), SessionSigner(b"z" * 32)
    sid = signer.new_session_id()
    assert signer.verify(sid, signer.token_for(sid))
    assert not signer.verify(sid, other.token_for(sid))
    assert not signer.verify("short", signer.token_for(sid))
    assert not signer.verify(sid, "")
    assert not signer.verify(sid, "é" * 64)  # non-ascii never raises


# ------------------------------------------------------------------------------------------ CSRF

UNSAFE = [
    ("POST", "/api/stop"),
    ("POST", "/api/run"),
    ("PUT", "/api/profile"),
    ("PUT", "/api/settings"),
    ("PUT", "/api/search"),
    ("POST", "/api/answers"),
    ("PUT", "/api/answers/1"),
    ("DELETE", "/api/answers/1"),
    ("POST", "/api/pending-questions/1/resolve"),
    ("POST", "/api/applications/x/mark-applied"),
    ("POST", "/api/resume"),
    ("POST", "/api/unstop"),
    ("PUT", "/api/kb"),
    ("POST", "/api/kb/from-resume"),
    ("POST", "/api/workbook/inspect"),
]


@pytest.mark.parametrize(("method", "path"), UNSAFE)
def test_every_state_changing_route_needs_the_csrf_token(
    bare_client: TestClient, method: str, path: str
) -> None:
    assert bare_client.request(method, path, json={}).status_code == 403  # no cookie, no token
    bare_client.get("/")  # obtain the session cookie
    response = bare_client.request(method, path, json={})  # cookie but no header
    assert response.status_code == 403 and response.json()["code"] == "csrf_failed"
    bad = bare_client.request(method, path, json={}, headers={"X-CSRF-Token": "0" * 64})
    assert bad.status_code == 403
    token = bare_client.get("/api/csrf").json()["csrf_token"]
    ok = bare_client.request(method, path, json={}, headers={"X-CSRF-Token": token})
    assert ok.status_code != 403


def test_token_from_another_session_is_rejected(app: Any) -> None:
    with TestClient(app, base_url=BASE_URL) as a, TestClient(app, base_url=BASE_URL) as b:
        token_a = a.get("/api/csrf").json()["csrf_token"]
        b.get("/api/csrf")
        assert b.post("/api/stop", headers={"X-CSRF-Token": token_a}).status_code == 403


def test_token_without_the_cookie_is_rejected(app: Any) -> None:
    with TestClient(app, base_url=BASE_URL) as a:
        token = a.get("/api/csrf").json()["csrf_token"]
    with TestClient(app, base_url=BASE_URL) as fresh:
        assert fresh.post("/api/stop", headers={"X-CSRF-Token": token}).status_code == 403


def test_garbage_cookie_is_replaced_not_trusted(bare_client: TestClient) -> None:
    bare_client.cookies.set("autoapply_session", "not-a-valid-id")
    response = bare_client.get("/api/csrf")
    assert "set-cookie" in response.headers
    assert bare_client.post("/api/stop", headers={"X-CSRF-Token": "x"}).status_code == 403


def test_restart_invalidates_old_tokens_but_reload_recovers(runtime: Any) -> None:
    with TestClient(create_app(runtime), base_url=BASE_URL) as client:
        old = client.get("/api/csrf").json()["csrf_token"]
        cookies = dict(client.cookies)
    with TestClient(create_app(runtime), base_url=BASE_URL, cookies=cookies) as client2:
        assert client2.post("/api/stop", headers={"X-CSRF-Token": old}).status_code == 403
        fresh = client2.get("/api/csrf").json()["csrf_token"]
        assert client2.post("/api/stop", headers={"X-CSRF-Token": fresh}).status_code == 200


def test_get_requests_need_no_token(bare_client: TestClient) -> None:
    assert bare_client.get("/api/state").status_code == 200


# ------------------------------------------------------------------------------------------ cross-site


def test_mismatching_origin_is_rejected_even_with_a_valid_token(client: TestClient) -> None:
    for origin in ("http://evil.example", "http://127.0.0.1:9999", "http://localhost:8765", "null"):
        response = client.post("/api/unstop", headers={"Origin": origin})
        assert response.status_code == 403, origin
        assert response.json()["code"] == "cross_origin"
        assert client.get("/api/state", headers={"Origin": origin}).status_code == 403


def test_matching_origin_is_accepted(client: TestClient) -> None:
    assert client.post("/api/unstop", headers={"Origin": BASE_URL}).status_code == 200


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_cross_site_fetch_metadata_is_rejected(client: TestClient, site: str) -> None:
    assert client.post("/api/unstop", headers={"Sec-Fetch-Site": site}).status_code == 403
    assert client.get("/api/state", headers={"Sec-Fetch-Site": site}).status_code == 403
    assert client.get("/files/artifacts/x.png", headers={"Sec-Fetch-Site": site}).status_code == 403


def test_same_origin_fetch_metadata_is_accepted(client: TestClient) -> None:
    assert client.post("/api/unstop", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert client.get("/api/state", headers={"Sec-Fetch-Site": "none"}).status_code == 200


def test_cross_site_link_click_to_a_page_is_allowed_but_not_a_post(client: TestClient) -> None:
    nav = {
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    }
    assert client.get("/", headers=nav).status_code == 200
    assert client.post("/api/unstop", headers=nav).status_code == 403


# ------------------------------------------------------------------------------------------ limits


def test_oversized_json_bodies_are_refused(client: TestClient) -> None:
    big = {"first_name": "x" * (3 * 1024 * 1024)}
    response = client.put("/api/profile", json=big)
    assert response.status_code == 413 and response.json()["code"] == "body_too_large"
    assert_secure_headers(response)


def test_oversized_streamed_bodies_without_content_length_are_cut(client: TestClient) -> None:
    def chunks() -> Any:
        for _ in range(40):
            yield b"x" * (100 * 1024)

    response = client.put(
        "/api/profile", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
