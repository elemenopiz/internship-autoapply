"""/api/state and the profile / settings / search endpoints."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient

from autoapply.config import AppPaths, load_config
from autoapply.models import ApplicationStatus, RunMode

from .conftest import FICTIONAL_KEY, ready_profile

# ------------------------------------------------------------------------------------------ state


def test_state_defaults(client: TestClient) -> None:
    state = client.get("/api/state").json()
    assert state["mode"] == "full_auto" and state["daily_cap"] == 5
    assert state["submitted_today"] == 0 and state["cap_remaining"] == 5
    assert state["cap_reached"] is False and state["stop_file"] is False
    assert state["running"] is False and state["schedule"]["enabled"] is False
    assert state["platforms"]["linkedin"] is False and state["automation_risk_platforms"] == []
    assert state["readiness"]["ok"] is False
    codes = {i["code"] for i in state["readiness"]["issues"]}
    assert {"profile_field_missing", "resume_missing", "openai_key_missing"} <= codes
    assert set(state["applications_by_status"]) == {s.value for s in ApplicationStatus}
    assert state["secrets"] == {"openai_key": {"present": False, "source": None}}


def test_state_counts_todays_submissions_by_local_day(
    client: TestClient, seed: Any, repo: Any, fake_clock: Any
) -> None:
    for i in range(2):
        seed(title=f"Role {i}", status=ApplicationStatus.SUBMITTED)
    seed(title="Dry", status=ApplicationStatus.SUBMITTED, mode=RunMode.DRY_RUN)  # never counts
    seed(title="Failed", status=ApplicationStatus.FAILED)
    state = client.get("/api/state").json()
    assert state["submitted_today"] == 2 and state["cap_remaining"] == 3
    assert state["applications_by_status"]["submitted"] == 3
    # Next local calendar day in America/Chicago: yesterday's submissions no longer count.
    fake_clock.advance(timedelta(days=1))
    assert client.get("/api/state").json()["submitted_today"] == 0


def test_state_cap_reached_and_timezone_boundary(
    client: TestClient, seed: Any, fake_clock: Any
) -> None:
    client.put("/api/settings", json={"daily_cap": 1})
    seed(status=ApplicationStatus.SUBMITTED_UNCONFIRMED)
    state = client.get("/api/state").json()
    assert state["cap_reached"] is True and state["cap_remaining"] == 0
    assert state["today"] == "2026-09-29"
    client.put("/api/settings", json={"timezone": "Pacific/Auckland"})  # already 30 Sep there
    assert client.get("/api/state").json()["today"] == "2026-09-30"


def test_state_reports_controller_schedule_stop_and_pending(
    client: TestClient, controller: Any, paths: AppPaths, repo: Any
) -> None:
    from autoapply.models import PendingQuestion

    controller.next_run_at = "2026-09-30T14:30:00+00:00"
    controller.running = True
    paths.stop_file.write_text("x")
    repo.add_pending_question(PendingQuestion(question="Do you have a clearance?"))
    state = client.get("/api/state").json()
    assert state["running"] is True and state["stop_file"] is True
    assert state["schedule"]["next_run_at"] == "2026-09-30T14:30:00+00:00"
    assert state["controller"]["next_run_at"] == "2026-09-30T14:30:00+00:00"
    assert state["pending_questions"] == 1


def test_state_survives_a_broken_controller(client: TestClient, controller: Any) -> None:
    controller.broken = True
    state = client.get("/api/state").json()
    assert "error" in state["controller"]


def test_state_never_exposes_the_key(client: TestClient, runtime: Any, make_ready: Any) -> None:
    make_ready()
    body = client.get("/api/state").text
    assert FICTIONAL_KEY not in body and FICTIONAL_KEY[-6:] not in body and "sk-" not in body
    state = json.loads(body)
    assert state["secrets"]["openai_key"] == {"present": True, "source": "env"}
    assert state["readiness"]["ok"] is True


def test_state_key_source_credential_store(client: TestClient, runtime: Any) -> None:
    from autoapply.secrets import set_stored_openai_key

    assert runtime.store is not None
    set_stored_openai_key(runtime.store, FICTIONAL_KEY)
    body = client.get("/api/state").text
    assert FICTIONAL_KEY not in body
    assert json.loads(body)["secrets"]["openai_key"] == {
        "present": True,
        "source": "credential_store",
    }


def test_state_linkedin_indeed_risk_flag(client: TestClient) -> None:
    client.put("/api/settings", json={"platforms": {"linkedin": True}})
    state = client.get("/api/state").json()
    assert state["automation_risk_platforms"] == ["linkedin"] and state["automation_risk_message"]


def test_config_is_reloaded_on_every_request(client: TestClient, paths: AppPaths) -> None:
    assert client.get("/api/settings").json()["daily_cap"] == 5
    paths.config_file.write_text(json.dumps({"daily_cap": 9, "future_key": 1}), encoding="utf-8")
    assert client.get("/api/settings").json()["daily_cap"] == 9
    assert client.get("/api/state").json()["daily_cap"] == 9


def test_invalid_config_file_is_a_clean_error(client: TestClient, paths: AppPaths) -> None:
    paths.config_file.write_text("{not json", encoding="utf-8")
    response = client.get("/api/state")
    assert response.status_code == 500 and response.json()["code"] == "config_invalid"
    page = client.get("/")
    assert page.status_code == 500 and "config.json" in page.text
    paths.config_file.write_text(json.dumps({"daily_cap": "many"}), encoding="utf-8")
    assert client.get("/api/settings").json()["code"] == "config_invalid"


def test_invalid_timezone_in_file_does_not_break_state(client: TestClient, paths: AppPaths) -> None:
    paths.config_file.write_text(json.dumps({"timezone": "Mars/Base"}), encoding="utf-8")
    state = client.get("/api/state").json()
    assert state["timezone_valid"] is False and state["submitted_today"] == 0


# ------------------------------------------------------------------------------------------ profile


def test_profile_roundtrip_and_partial_merge(client: TestClient, paths: AppPaths) -> None:
    assert client.get("/api/profile").json()["first_name"] == ""
    response = client.put("/api/profile", json=ready_profile())
    assert response.status_code == 200 and response.json()["first_name"] == "Ada"
    assert response.json()["full_name"] == "Ada Testperson"
    assert load_config(paths).profile.email == "ada.testperson@example.test"
    client.put("/api/profile", json={"city": "Dallas", "eeo": {"gender": "female"}})
    profile = client.get("/api/profile").json()
    assert profile["city"] == "Dallas" and profile["first_name"] == "Ada"
    assert profile["eeo"]["gender"] == "female" and profile["eeo"]["race_ethnicity"] == "decline"
    # GET -> PUT round trip (includes the computed full_name) is accepted.
    assert client.put("/api/profile", json=profile).status_code == 200


def test_profile_normalises_dates_and_urls(client: TestClient) -> None:
    profile = client.put(
        "/api/profile",
        json={"graduation_date": "May 2028", "linkedin_url": "linkedin.com/in/ada-test"},
    ).json()
    assert profile["graduation_date"] == "2028-05"
    assert profile["linkedin_url"] == "https://linkedin.com/in/ada-test"


def test_profile_validation_errors_are_422_with_field_locs(
    client: TestClient, paths: AppPaths
) -> None:
    before = paths.config_file.exists()
    response = client.put(
        "/api/profile",
        json={
            "email": "nope",
            "phone": "123",
            "graduation_date": "soon",
            "authorized_to_work_us": "maybe",
            "github_url": "ftp://x.example",
            "available_start_date": "2027-13-01",
            "frist_name": "typo",
            "eeo": {"gender": "x" * 500},
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error" and body["errors"] == body["detail"]
    locs = {tuple(e["loc"]) for e in body["errors"]}
    assert {("frist_name",), ("eeo", "gender")} <= locs
    response = client.put(
        "/api/profile",
        json={
            "email": "nope",
            "phone": "123",
            "graduation_date": "soon",
            "authorized_to_work_us": "maybe",
            "github_url": "ftp://x.example",
            "available_start_date": "2027-13-01",
        },
    )
    locs = {tuple(e["loc"]) for e in response.json()["errors"]}
    assert locs == {
        ("email",),
        ("phone",),
        ("graduation_date",),
        ("authorized_to_work_us",),
        ("github_url",),
        ("available_start_date",),
    }
    assert paths.config_file.exists() == before  # nothing was written


def test_profile_rejects_non_objects_and_control_characters(client: TestClient) -> None:
    assert client.put("/api/profile", json=["a"]).status_code == 422
    assert client.put("/api/profile", json={"first_name": "Ada\nEvil"}).status_code == 422
    assert (
        client.put("/api/profile", json={"fallback_resume_path": "/x/notes.txt"}).status_code == 422
    )
    assert (
        client.put(
            "/api/profile", content=b"{oops", headers={"Content-Type": "application/json"}
        ).status_code
        == 422
    )


def test_profile_put_preserves_unrelated_config(client: TestClient, paths: AppPaths) -> None:
    client.put("/api/settings", json={"daily_cap": 7})
    client.put("/api/profile", json={"first_name": "Ada"})
    config = load_config(paths)
    assert config.daily_cap == 7 and config.profile.first_name == "Ada"


# ------------------------------------------------------------------------------------------ settings


def test_settings_get_shape(client: TestClient) -> None:
    settings = client.get("/api/settings").json()
    assert set(settings) == {
        "mode",
        "daily_cap",
        "timezone",
        "schedule",
        "platforms",
        "workbook",
        "boards",
        "apply",
        "llm",
    }
    assert "profile" not in settings and "search" not in settings
    assert settings["apply"]["email"]["imap_port"] == 993


def test_settings_partial_update_and_roundtrip(client: TestClient, paths: AppPaths) -> None:
    body = {
        "mode": "dry_run",
        "daily_cap": 12,
        "timezone": "America/New_York",
        "apply": {
            "headless": False,
            "screenshots": "always",
            "min_delay_s": 5,
            "max_delay_s": 10,
            "attestations_authorized": True,
            "generic_portal": False,
            "email": {
                "enabled": True,
                "imap_host": "imap.example.test",
                "imap_port": 993,
                "username": "ada@example.test",
            },
        },
        "platforms": {"linkedin": True},
        "boards": {
            "greenhouse": ["stripe", " Stripe ", "notion-hq"],
            "lever": [],
            "ashby": ["a_b.c"],
        },
        "workbook": {"path": "  ", "sheet": "Verified"},
        "schedule": {
            "run_times": ["17:00", "09:30", "09:30"],
            "days_of_week": [4, 0, 0],
            "jitter_minutes": 3,
        },
        "llm": {"model": "gpt-4.1-mini"},
    }
    result = client.put("/api/settings", json=body).json()
    assert result["mode"] == "dry_run" and result["daily_cap"] == 12
    assert result["apply"]["email"]["imap_host"] == "imap.example.test"
    assert result["apply"]["email"]["enabled"] is True
    assert result["boards"]["greenhouse"] == ["stripe", "notion-hq"]
    assert result["workbook"]["path"] is None and result["workbook"]["sheet"] == "Verified"
    assert result["schedule"]["run_times"] == ["17:00", "09:30"]
    assert result["schedule"]["days_of_week"] == [0, 4]
    assert result["platforms"]["linkedin"] is True and result["platforms"]["greenhouse"] is True
    assert client.get("/api/settings").json() == result
    assert client.put("/api/settings", json=result).status_code == 200
    assert load_config(paths).apply.attestations_authorized is True


def test_settings_validation(client: TestClient, paths: AppPaths) -> None:
    def errors(body: dict[str, Any]) -> set[tuple[Any, ...]]:
        response = client.put("/api/settings", json=body)
        assert response.status_code == 422, body
        return {tuple(e["loc"]) for e in response.json()["errors"]}

    assert errors({"daily_cap": 51}) == {("daily_cap",)}
    assert errors({"daily_cap": -1}) == {("daily_cap",)}
    assert errors({"daily_cap": True}) == {("daily_cap",)}
    assert errors({"daily_cap": "5"}) == {("daily_cap",)}
    assert errors({"daily_cap": 2.5}) == {("daily_cap",)}
    assert errors({"timezone": "Mars/Base"}) == {("timezone",)}
    assert errors({"timezone": ""}) == {("timezone",)}
    assert errors({"timezone": "../../etc/passwd"}) == {("timezone",)}
    assert errors({"mode": "yolo"}) == {("mode",)}
    assert errors({"schedule": {"run_times": ["25:00"]}}) == {("schedule", "run_times")}
    assert errors({"schedule": {"run_times": []}}) == {("schedule", "run_times")}
    assert errors({"schedule": {"days_of_week": [7]}}) == {("schedule", "days_of_week")}
    assert errors({"schedule": {"days_of_week": []}}) == {("schedule", "days_of_week")}
    assert errors({"apply": {"screenshots": "sometimes"}}) == {("apply", "screenshots")}
    assert errors({"apply": {"min_delay_s": 100, "max_delay_s": 10}}) == {("apply", "min_delay_s")}
    assert errors({"apply": {"email": {"imap_port": 70000}}}) == {("apply", "email", "imap_port")}
    assert errors({"apply": {"email": {"imap_host": "bad host!"}}}) == {
        ("apply", "email", "imap_host")
    }
    assert errors({"boards": {"greenhouse": ["a/../b"]}}) == {("boards", "greenhouse")}
    assert errors({"boards": {"lever": ["https://jobs.lever.co/x"]}}) == {("boards", "lever")}
    assert errors({"boards": {"ashby": "x"}}) == {("boards", "ashby")}
    assert errors({"nonsense": 1}) == {("nonsense",)}
    assert errors({"apply": {"nonsense": 1}}) == {("apply", "nonsense")}
    assert errors({"profile": {"first_name": "x"}}) == {("profile",)}
    assert errors({"platforms": "yes"}) == {("platforms",)}
    assert errors({"llm": {"model": "bad model!"}}) == {("llm", "model")}
    assert errors({"daily_cap": 99, "timezone": "Nope/Nope"}) == {("daily_cap",), ("timezone",)}
    assert not paths.config_file.exists()


def test_settings_never_store_the_ack_flag(client: TestClient, paths: AppPaths) -> None:
    client.put("/api/settings", json={"daily_cap": 3, "acknowledge_no_dry_run": True})
    assert "acknowledge" not in paths.config_file.read_text()
    response = client.put("/api/settings", json={"acknowledge_no_dry_run": "yes"})
    assert response.status_code == 422


def test_dashboard_accepts_no_secrets(client: TestClient) -> None:
    for body in ({"openai_api_key": FICTIONAL_KEY}, {"apply": {"email": {"password": "hunter2"}}}):
        assert client.put("/api/settings", json=body).status_code == 422
    assert client.put("/api/profile", json={"openai_api_key": FICTIONAL_KEY}).status_code == 422


# ------------------------------------------------------------------------------------------ schedule gates


def test_enabling_the_schedule_needs_readiness(client: TestClient, paths: AppPaths) -> None:
    response = client.put("/api/settings", json={"schedule": {"enabled": True}})
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "not_ready" and body["issues"]
    assert {"code", "field", "message"} <= set(body["issues"][0])
    assert load_config(paths).schedule.enabled is False


def test_schedule_needs_dry_run_or_acknowledgement_in_full_auto(
    client: TestClient, make_ready: Any, paths: AppPaths, seed: Any
) -> None:
    make_ready()
    response = client.put("/api/settings", json={"schedule": {"enabled": True}})
    assert response.status_code == 409 and response.json()["code"] == "dry_run_recommended"
    assert load_config(paths).schedule.enabled is False
    ok = client.put(
        "/api/settings", json={"schedule": {"enabled": True}, "acknowledge_no_dry_run": True}
    )
    assert ok.status_code == 200 and ok.json()["schedule"]["enabled"] is True
    # Turning it off never needs anything.
    assert client.put("/api/settings", json={"schedule": {"enabled": False}}).status_code == 200
    # After a successful dry run no acknowledgement is required.
    seed(status=ApplicationStatus.DRY_RUN_OK, mode=RunMode.DRY_RUN)
    assert client.put("/api/settings", json={"schedule": {"enabled": True}}).status_code == 200


def test_dry_run_mode_schedule_needs_no_acknowledgement(
    client: TestClient, make_ready: Any
) -> None:
    make_ready(RunMode.DRY_RUN)
    assert client.put("/api/settings", json={"schedule": {"enabled": True}}).status_code == 200


def test_readiness_is_judged_on_the_resulting_config(client: TestClient, make_ready: Any) -> None:
    make_ready()
    client.put("/api/settings", json={"apply": {"attestations_authorized": False}})
    assert client.put("/api/settings", json={"schedule": {"enabled": True}}).status_code == 409
    both = {"mode": "dry_run", "schedule": {"enabled": True}}
    assert client.put("/api/settings", json=both).status_code == 200


def test_editing_an_enabled_schedule_is_not_regated(
    client: TestClient, make_ready: Any, paths: AppPaths
) -> None:
    make_ready()
    client.put(
        "/api/settings", json={"schedule": {"enabled": True}, "acknowledge_no_dry_run": True}
    )
    paths.resume_file.unlink()  # readiness now fails
    response = client.put(
        "/api/settings", json={"schedule": {"enabled": True, "run_times": ["08:00"]}}
    )
    assert response.status_code == 200


# ------------------------------------------------------------------------------------------ search


def test_search_roundtrip_and_normalisation(client: TestClient, paths: AppPaths) -> None:
    default = client.get("/api/search").json()
    assert (
        default["target_term"] == "Summer 2027" and "product_management" in default["role_families"]
    )
    body = {
        "target_term": "Summer 2027",
        "recent_days": 30,
        "min_score": 60.5,
        "us_only": False,
        "include_keywords": ["python", " Python ", "sql", ""],
        "preferred_locations": ["Austin, TX"],
        "company_denylist": ["Evil Corp"],
        "role_families": {"pm": {"keywords": ["product manager", "apm"], "weight": 0.9}},
    }
    saved = client.put("/api/search", json=body).json()
    assert saved["include_keywords"] == ["python", "sql"]
    assert saved["role_families"] == {"pm": {"keywords": ["product manager", "apm"], "weight": 0.9}}
    assert saved["min_score"] == 60.5 and saved["us_only"] is False
    assert client.get("/api/search").json() == saved
    assert load_config(paths).search.recent_days == 30
    client.put("/api/search", json={"min_score": 40})  # partial
    assert client.get("/api/search").json()["role_families"] == saved["role_families"]


def test_search_validation(client: TestClient) -> None:
    def errors(body: dict[str, Any]) -> set[tuple[Any, ...]]:
        response = client.put("/api/search", json=body)
        assert response.status_code == 422, body
        return {tuple(e["loc"]) for e in response.json()["errors"]}

    assert errors({"min_score": 101}) == {("min_score",)}
    assert errors({"min_score": "high"}) == {("min_score",)}
    assert errors({"recent_days": 0}) == {("recent_days",)}
    assert errors({"target_term": "  "}) == {("target_term",)}
    assert errors({"include_keywords": "python"}) == {("include_keywords",)}
    assert errors({"include_keywords": [1]}) == {("include_keywords",)}
    assert errors({"role_families": {"pm": {"keywords": []}}}) == {
        ("role_families", "pm", "keywords")
    }
    assert errors({"role_families": {"pm": {"keywords": ["a"], "weight": 2}}}) == {
        ("role_families", "pm", "weight")
    }
    assert errors({"role_families": {"pm": {"weight": 1}}}) == {("role_families", "pm", "keywords")}
    assert errors({"role_families": {"pm": {"keywords": ["a"], "bogus": 1}}}) == {
        ("role_families", "pm", "bogus")
    }
    assert errors({"role_families": ["x"]}) == {("role_families",)}
    assert errors({"minscore": 5}) == {("minscore",)}
