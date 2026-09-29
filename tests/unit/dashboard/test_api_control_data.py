"""Run control, STOP file, opportunities, applications, runs, screening answers, pending questions."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from autoapply.config import AppPaths
from autoapply.models import (
    ApplicationStatus,
    ApplyResult,
    PendingQuestion,
    QuestionKind,
    Reason,
    RunMode,
    RunReport,
    ScoreResult,
)

from .conftest import HOSTILE

# ------------------------------------------------------------------------------------------ run control


def test_run_refused_when_not_ready(client: TestClient, controller: Any) -> None:
    response = client.post("/api/run", json={})
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "not_ready" and body["issues"]
    assert controller.calls == []


def test_discover_only_needs_no_readiness_beyond_a_source(
    client: TestClient, controller: Any
) -> None:
    client.put("/api/settings", json={"boards": {"greenhouse": ["acme"]}})
    response = client.post("/api/run", json={"mode": "discover_only"})
    assert response.status_code == 202
    assert response.json() == {"accepted": True, "mode": "discover_only", "running": True}
    assert controller.calls == [(RunMode.DISCOVER_ONLY, "manual")]


def test_run_when_ready_uses_config_mode_or_override(
    client: TestClient, controller: Any, make_ready: Any
) -> None:
    make_ready()
    assert client.post("/api/run").status_code == 202  # no body at all
    assert controller.calls == [(None, "manual")]
    controller.running = False
    assert client.post("/api/run", json={"mode": "dry_run"}).status_code == 202
    assert controller.calls[-1] == (RunMode.DRY_RUN, "manual")


def test_run_readiness_uses_the_requested_mode(
    client: TestClient, controller: Any, make_ready: Any
) -> None:
    make_ready()
    client.put("/api/settings", json={"apply": {"attestations_authorized": False}})
    assert client.post("/api/run", json={}).status_code == 409  # full_auto needs the attestation
    assert client.post("/api/run", json={"mode": "dry_run"}).status_code == 202


def test_run_conflicts_and_validation(client: TestClient, controller: Any, make_ready: Any) -> None:
    make_ready()
    controller.running = True
    response = client.post("/api/run", json={})
    assert response.status_code == 409 and response.json()["code"] == "already_running"
    controller.running = False
    controller.accept = False  # controller races us and refuses
    assert client.post("/api/run", json={}).json()["code"] == "already_running"
    controller.accept = True
    assert client.post("/api/run", json={"mode": "bogus"}).status_code == 422
    assert client.post("/api/run", json=["x"]).status_code == 422


def test_stop_and_resume_toggle_the_stop_file(
    client: TestClient, controller: Any, paths: AppPaths, make_ready: Any
) -> None:
    make_ready()
    assert not paths.stop_file.exists()
    response = client.post("/api/stop")
    assert response.status_code == 200 and response.json()["stop_file"] is True
    assert paths.stop_file.exists() and controller.stop_requests == 1
    assert client.get("/api/state").json()["stop_file"] is True
    client.post("/api/stop")  # idempotent
    assert paths.stop_file.exists()
    blocked = client.post("/api/run", json={})
    assert blocked.status_code == 409 and blocked.json()["code"] == "stop_active"
    assert client.post("/api/run", json={"mode": "discover_only"}).status_code == 202
    resumed = client.post("/api/resume")  # overloaded route: no multipart -> clears STOP
    assert resumed.json() == {"stop_file": False, "resumed": True}
    assert not paths.stop_file.exists()
    assert client.post("/api/unstop").json()["resumed"] is True  # alias, nothing to remove
    assert client.get("/api/state").json()["stop_file"] is False


def test_resume_with_json_body_also_clears_stop(client: TestClient, paths: AppPaths) -> None:
    client.post("/api/stop")
    assert client.post("/api/resume", json={}).json()["resumed"] is True
    assert not paths.stop_file.exists()


# ------------------------------------------------------------------------------------------ opportunities


def test_opportunities_list_filters_and_reasons(client: TestClient, seed: Any, repo: Any) -> None:
    a = seed("Acme Robotics", "Product Management Intern", source="workbook")
    b = seed("Globex", "Data Analyst Intern", source="lever", location="Remote")
    c = seed("Initech", "Strategy Intern", status=ApplicationStatus.NEEDS_MANUAL)
    repo.set_score(
        a.id, ScoreResult(score=82.5, passed=True, reasons=["Title matches product_management"])
    )
    repo.set_score(b.id, ScoreResult(score=40, passed=False, penalties=["Below threshold"]))
    body = client.get("/api/opportunities").json()
    assert body["total"] == 3 and body["limit"] == 50 and body["offset"] == 0
    assert [i["company"] for i in body["items"]][:2] == ["Acme Robotics", "Globex"]  # score desc
    first = body["items"][0]
    assert (
        first["score"]["reasons"] == ["Title matches product_management"]
        and first["score"]["passed"]
    )
    assert "description" not in first and "description_snippet" in first
    assert client.get("/api/opportunities?min_score=50").json()["total"] == 1
    assert client.get("/api/opportunities?passed_only=true").json()["total"] == 1
    assert client.get("/api/opportunities?source=lever").json()["items"][0]["id"] == b.id
    assert client.get("/api/opportunities?search=glob").json()["total"] == 1
    assert client.get("/api/opportunities?status=needs_manual").json()["items"][0]["id"] == c.id
    assert client.get("/api/opportunities?status=unapplied").json()["total"] == 2
    paged = client.get("/api/opportunities?limit=1&offset=1").json()
    assert paged["total"] == 3 and len(paged["items"]) == 1 and paged["items"][0]["id"] == b.id
    assert paged["items"][0]["latest_application"] is None
    assert (
        client.get("/api/opportunities?status=needs_manual").json()["items"][0][
            "latest_application"
        ]["status"]
        == "needs_manual"
    )


def test_opportunities_query_validation(client: TestClient) -> None:
    for query in (
        "min_score=abc",
        "min_score=101",
        "limit=0",
        "limit=501",
        "offset=-1",
        "status=bogus",
        "source=bogus",
        "order=random",
        "search=" + "x" * 201,
    ):
        assert client.get(f"/api/opportunities?{query}").status_code == 422, query
    assert client.get("/api/opportunities?status=&source=").status_code == 200  # blank = no filter


def test_opportunity_detail_and_404(client: TestClient, seed: Any) -> None:
    op = seed(
        description="A long description " * 40, extra={"note": "x"}, status=ApplicationStatus.FAILED
    )
    detail = client.get(f"/api/opportunities/{op.id}").json()
    assert detail["description"].startswith("A long description") and detail["extra"] == {
        "note": "x"
    }
    assert detail["applications"][0]["status"] == "failed"
    assert client.get("/api/opportunities/doesnotexist").status_code == 404


# ------------------------------------------------------------------------------------------ applications


def test_applications_list_detail_and_filters(
    client: TestClient, seed: Any, repo: Any, paths: AppPaths
) -> None:
    op = seed("Acme Robotics", "PM Intern")
    app = repo.create_application(op.id, RunMode.FULL_AUTO)
    (paths.artifacts_dir / str(app.id)).mkdir(parents=True)
    (paths.artifacts_dir / str(app.id) / "step1.png").write_bytes(b"\x89PNG")
    (paths.documents_dir / op.id).mkdir(parents=True)
    (paths.documents_dir / op.id / "resume.pdf").write_bytes(b"%PDF-")
    repo.finish_application(
        app.id,
        ApplyResult(
            status=ApplicationStatus.NEEDS_MANUAL,
            reason=Reason.MISSING_ANSWER,
            message="No saved answer",
            artifacts=[f"artifacts/{app.id}/step1.png", "C:/elsewhere/secret.png"],
            steps=["opened", "stopped"],
            filled_fields={"first_name": "Ada"},
        ),
        docs={"resume": str(paths.documents_dir / op.id / "resume.pdf"), "mode": "tailored"},
    )
    seed("Globex", "Analyst", status=ApplicationStatus.SUBMITTED)
    body = client.get("/api/applications").json()
    assert body["total"] == 2 and {i["company"] for i in body["items"]} == {
        "Acme Robotics",
        "Globex",
    }
    row = next(i for i in body["items"] if i["company"] == "Acme Robotics")
    assert (
        row["status"] == "needs_manual"
        and row["reason"] == "missing_answer"
        and row["reason_label"]
    )
    assert row["artifacts"][0] == {
        "label": "step1.png",
        "name": "step1.png",
        "url": f"/files/artifacts/{app.id}/step1.png",
        "path": f"artifacts/{app.id}/step1.png",
    }
    assert row["artifacts"][1]["url"] is None and row["artifacts"][1]["name"] == "secret.png"
    assert row["documents"][0]["url"] == f"/files/documents/{op.id}/resume.pdf"
    assert row["docs_mode"] == "tailored" and "steps" not in row
    only = client.get("/api/applications?status=needs_manual").json()
    assert only["total"] == 1
    assert client.get("/api/applications?status=submitted,needs_manual").json()["total"] == 2
    assert client.get(f"/api/applications?opportunity_id={op.id}").json()["total"] == 1
    assert client.get("/api/applications?status=bogus").status_code == 422
    detail = client.get(f"/api/applications/{app.id}").json()
    assert detail["steps"] == ["opened", "stopped"] and detail["filled_fields"] == {
        "first_name": "Ada"
    }
    assert "C:/elsewhere" not in str(detail["documents"]) and detail["company"] == "Acme Robotics"
    assert client.get("/api/applications/9999").status_code == 404
    assert client.get("/api/applications/abc").status_code == 422


def test_mark_applied_manually(client: TestClient, seed: Any, repo: Any) -> None:
    op = seed(status=ApplicationStatus.NEEDS_MANUAL)
    response = client.post(f"/api/applications/{op.id}/mark-applied")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "skipped" and body["reason"] == "already_applied"
    assert repo.has_submitted(op.id)
    again = client.post(f"/api/applications/{op.id}/mark-applied").json()
    assert again["id"] == body["id"]  # idempotent
    assert client.post("/api/applications/unknown/mark-applied").status_code == 404
    assert client.get("/api/state").json()["submitted_today"] == 0  # never counts toward the cap


def test_runs_listing(client: TestClient, repo: Any) -> None:
    run_id = repo.start_run(RunMode.DRY_RUN, "manual")
    repo.finish_run(
        run_id,
        RunReport(discovered=5, new=2, submitted=0, dry_run_ok=1, stopped_reason="cap_reached"),
    )
    repo.start_run(RunMode.FULL_AUTO, "schedule")
    body = client.get("/api/runs").json()
    assert [r["trigger"] for r in body["items"]] == ["schedule", "manual"]
    assert (
        body["items"][1]["discovered"] == 5 and body["items"][1]["stopped_reason"] == "cap_reached"
    )
    assert body["items"][0]["finished_at"] is None
    assert len(client.get("/api/runs?limit=1").json()["items"]) == 1
    assert client.get("/api/runs?limit=0").status_code == 422


# ------------------------------------------------------------------------------------------ answers


def test_answers_crud(client: TestClient) -> None:
    assert client.get("/api/answers").json() == {"items": [], "total": 0}
    created = client.post(
        "/api/answers",
        json={
            "question": "Have you been convicted of a felony?",
            "answer": "No",
            "intent": "felony_conviction",
            "answer_kind": "boolean",
        },
    )
    assert created.status_code == 200
    answer = created.json()
    assert answer["source"] == "user" and answer["intent"] == "felony_conviction" and answer["id"]
    assert client.get("/api/answers").json()["total"] == 1
    updated = client.put(
        f"/api/answers/{answer['id']}", json={"answer": "Prefer to discuss"}
    ).json()
    assert updated["answer"] == "Prefer to discuss" and updated["question"] == answer["question"]
    assert updated["intent"] == "felony_conviction" and updated["id"] == answer["id"]
    renamed = client.put(
        f"/api/answers/{answer['id']}", json={"question": "Felony conviction?"}
    ).json()
    assert (
        renamed["question"] == "Felony conviction?"
        and renamed["question_norm"] == "felony conviction"
    )
    same_intent = client.post(
        "/api/answers", json={"answer": "Yes", "intent": "felony_conviction"}
    ).json()
    assert same_intent["id"] == answer["id"]  # upsert by intent
    assert client.delete(f"/api/answers/{answer['id']}").json() == {
        "deleted": True,
        "id": answer["id"],
    }
    assert client.get("/api/answers").json()["total"] == 0
    assert client.delete(f"/api/answers/{answer['id']}").status_code == 404
    assert client.put("/api/answers/999", json={"answer": "x"}).status_code == 404


def test_answers_by_question_text_only(client: TestClient) -> None:
    stored = client.post(
        "/api/answers", json={"question": "Preferred pronouns?", "answer": "she/her"}
    ).json()
    assert stored["intent"] is None and stored["answer_kind"] == "text"
    assert (
        client.post(
            "/api/answers", json={"question": "Preferred pronouns?", "answer": "they"}
        ).json()["id"]
        == stored["id"]
    )


def test_answers_validation(client: TestClient) -> None:
    def errors(body: dict[str, Any]) -> set[str]:
        response = client.post("/api/answers", json=body)
        assert response.status_code == 422, body
        return {e["loc"][0] for e in response.json()["errors"]}

    assert errors({}) == {"answer", "question"}
    assert errors({"question": "q"}) == {"answer"}
    assert errors({"answer": "a"}) == {"question"}
    assert errors({"question": "q", "answer": "   "}) == {"answer"}
    assert errors({"question": "q", "answer": "a", "answer_kind": "blob"}) == {"answer_kind"}
    assert errors({"question": "q", "answer": "a", "intent": "Bad Intent!"}) == {"intent"}
    assert errors({"question": "q" * 501, "answer": "a"}) == {"question"}
    assert errors({"question": "q", "answer": "a" * 2001}) == {"answer"}
    assert errors({"question": "q", "answer": "a", "id": 3}) == {"id"}
    assert errors({"question": "line\nbreak", "answer": "a"}) == {"question"}
    assert (
        client.post("/api/answers", json={"question": "q", "answer": "multi\nline ok"}).status_code
        == 200
    )


def test_changing_an_intent_to_one_in_use_is_a_422(client: TestClient) -> None:
    client.post("/api/answers", json={"question": "a", "answer": "1", "intent": "non_compete"})
    other = client.post("/api/answers", json={"question": "b", "answer": "2"}).json()
    response = client.put(f"/api/answers/{other['id']}", json={"intent": "non_compete"})
    assert response.status_code == 422


# ------------------------------------------------------------------------------------------ pending questions


def test_pending_questions_resolution_saves_a_reusable_answer(
    client: TestClient, repo: Any, seed: Any
) -> None:
    op = seed()
    q1 = repo.add_pending_question(
        PendingQuestion(
            question="Are you a US person for export control?",
            opportunity_id=op.id,
            company="Acme Robotics",
            kind=QuestionKind.SINGLE_CHOICE,
            options=["Yes", "No"],
        )
    )
    repo.add_pending_question(PendingQuestion(question="Salary expectation?"))
    body = client.get("/api/pending-questions").json()
    assert body["total"] == 2 and body["items"][0]["options"] == ["Yes", "No"]
    result = client.post(f"/api/pending-questions/{q1.id}/resolve", json={"answer": "Yes"})
    assert result.status_code == 200
    assert (
        result.json()["question"]["resolved"] is True
        and result.json()["saved_answer"]["answer"] == "Yes"
    )
    assert client.get("/api/pending-questions").json()["total"] == 1
    assert client.get("/api/pending-questions?include_resolved=true").json()["total"] == 2
    assert repo.find_answer(question_norm="Are you a US person for export control?").answer == "Yes"
    assert client.get("/api/answers").json()["total"] == 1


def test_pending_question_resolution_errors(client: TestClient, repo: Any) -> None:
    q = repo.add_pending_question(PendingQuestion(question="Anything else?"))
    assert client.post(f"/api/pending-questions/{q.id}/resolve", json={}).status_code == 422
    assert (
        client.post(f"/api/pending-questions/{q.id}/resolve", json={"answer": "  "}).status_code
        == 422
    )
    assert (
        client.post(f"/api/pending-questions/{q.id}/resolve", json={"answer": 5}).status_code == 422
    )
    assert (
        client.post("/api/pending-questions/999/resolve", json={"answer": "x"}).status_code == 404
    )
    assert client.get("/api/pending-questions").json()["total"] == 1


def test_hostile_strings_pass_through_the_json_api_unchanged(client: TestClient, seed: Any) -> None:
    seed(HOSTILE, HOSTILE)
    item = client.get("/api/opportunities").json()["items"][0]
    assert item["company"] == HOSTILE  # JSON is data; escaping is the pages' job
    stored = client.post("/api/answers", json={"question": HOSTILE, "answer": HOSTILE}).json()
    assert stored["answer"] == HOSTILE
