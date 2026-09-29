"""Resume upload, knowledge base, workbook inspector and artifact serving."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from autoapply.config import AppPaths, load_config
from autoapply.contracts import LLMError
from autoapply.dashboard.deps import DashboardRuntime
from autoapply.dashboard.files import resolve_served_file, served_file_ref
from autoapply.llm import FakeLLM
from autoapply.models import Experience, KnowledgeBase

from .conftest import BASE_URL, FICTIONAL_KEY, PDF_BYTES

MB = 1024 * 1024


def upload(client: TestClient, name: str, data: bytes, path: str = "/api/resume/upload") -> Any:
    return client.post(path, files={"file": (name, data, "application/pdf")})


# ------------------------------------------------------------------------------------------ upload


def test_upload_stores_the_pdf_atomically_and_points_config_at_it(
    client: TestClient, paths: AppPaths
) -> None:
    assert client.get("/api/resume").json()["present"] is False
    response = upload(client, "My Resume.pdf", PDF_BYTES)
    assert response.status_code == 200
    body = response.json()
    assert body["present"] and body["size"] == len(PDF_BYTES) and body["source"] == "uploaded"
    assert body["filename"] == "resume.pdf" and body["location"] == "profile/resume.pdf"
    assert paths.resume_file.read_bytes() == PDF_BYTES
    assert load_config(paths).profile.fallback_resume_path == str(paths.resume_file)
    assert [p.name for p in paths.profile_dir.iterdir() if p.is_file()] == [
        "resume.pdf"
    ]  # no temp files
    assert client.get("/api/resume").json()["modified"]
    codes = {i["code"] for i in client.get("/api/state").json()["readiness"]["issues"]}
    assert "resume_missing" not in codes


def test_upload_works_through_the_overloaded_route_and_replaces_the_old_file(
    client: TestClient, paths: AppPaths
) -> None:
    upload(client, "a.pdf", PDF_BYTES)
    newer = PDF_BYTES + b"% second version\n"
    assert upload(client, "b.PDF", newer, "/api/resume").status_code == 200
    assert paths.resume_file.read_bytes() == newer


@pytest.mark.parametrize(
    ("name", "data", "status"),
    [
        ("resume.pdf", b"\x89PNG\r\n\x1a\n" + b"0" * 100, 415),  # wrong content, right extension
        ("resume.txt", PDF_BYTES, 415),  # right content, wrong extension
        ("resume", PDF_BYTES, 415),
        ("resume.pdf.exe", PDF_BYTES, 415),
        ("resume.pdf", b"", 415),
        ("resume.pdf", b"  %PDF-1.4 leading space", 415),
        ("resume.pdf", b"<html>%PDF-</html>", 415),
    ],
)
def test_upload_rejects_non_pdfs(
    client: TestClient, paths: AppPaths, name: str, data: bytes, status: int
) -> None:
    response = upload(client, name, data)
    assert response.status_code == status and response.json()["code"] == "not_a_pdf"
    assert not paths.resume_file.exists()
    assert not paths.config_file.exists()


def test_rejected_upload_keeps_the_previous_resume(client: TestClient, paths: AppPaths) -> None:
    upload(client, "a.pdf", PDF_BYTES)
    assert upload(client, "b.pdf", b"not a pdf").status_code == 415
    assert paths.resume_file.read_bytes() == PDF_BYTES
    assert [p.name for p in paths.profile_dir.iterdir() if p.is_file()] == ["resume.pdf"]


def test_upload_size_limits(client: TestClient, paths: AppPaths) -> None:
    exactly = PDF_BYTES + b"0" * (10 * MB - len(PDF_BYTES))
    assert upload(client, "max.pdf", exactly).status_code == 200
    assert paths.resume_file.stat().st_size == 10 * MB
    too_big = exactly + b"0"  # one byte over: rejected by the handler
    response = upload(client, "big.pdf", too_big)
    assert response.status_code == 413 and response.json()["code"] == "file_too_large"
    assert paths.resume_file.stat().st_size == 10 * MB  # previous file untouched
    assert [p.name for p in paths.profile_dir.iterdir() if p.is_file()] == ["resume.pdf"]
    huge = upload(client, "huge.pdf", PDF_BYTES + b"0" * (12 * MB))  # cut while streaming
    assert huge.status_code == 413 and huge.json()["code"] == "body_too_large"


@pytest.mark.parametrize(
    "name", ["../../evil.pdf", "..\\..\\evil.pdf", "C:\\Windows\\x.pdf", "/etc/x.pdf", "a/b/c.pdf"]
)
def test_path_like_filenames_never_choose_where_the_file_goes(
    client: TestClient, paths: AppPaths, tmp_path: Path, name: str
) -> None:
    before = set(tmp_path.rglob("*"))
    response = upload(client, name, PDF_BYTES)
    assert response.status_code == 200
    assert response.json()["location"] == "profile/resume.pdf"
    created = set(tmp_path.rglob("*")) - before
    assert all(
        p == paths.resume_file or p == paths.config_file or paths.root in p.parents for p in created
    )
    assert not any(p.name == "evil.pdf" for p in created)


def test_upload_form_errors(client: TestClient) -> None:
    assert (
        client.post(
            "/api/resume/upload", data={"file": "text"}, files={"other": ("x.pdf", PDF_BYTES)}
        ).status_code
        == 422
    )
    assert client.post("/api/resume/upload", data={"file": "not a file part"}).status_code == 422


def test_upload_alias_requires_multipart(client: TestClient) -> None:
    response = client.post("/api/resume/upload", json={})
    assert response.status_code == 422 and response.json()["errors"][0]["loc"] == ["file"]


def test_get_resume_never_reveals_paths_outside_data(
    client: TestClient, paths: AppPaths, tmp_path: Path
) -> None:
    outside = tmp_path / "private folder" / "my resume.pdf"
    outside.parent.mkdir()
    outside.write_bytes(PDF_BYTES)
    client.put("/api/profile", json={"fallback_resume_path": str(outside)})
    body = client.get("/api/resume")
    view = body.json()
    assert view["present"] is True and view["source"] == "configured_path"
    assert (
        view["location"] is None
        and "private folder" not in body.text
        and "my resume" not in body.text
    )


# ------------------------------------------------------------------------------------------ knowledge base


def kb_payload(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source": "resume",
        "skills": ["Python", " SQL ", "python"],
        "experiences": [
            {
                "id": "acme-intern",
                "kind": "work",
                "title": "Product Intern",
                "organization": "Acme",
                "location": "",
                "start": "2025-06",
                "end": "present",
                "bullets": ["Shipped a thing", "  "],
                "skills": ["SQL"],
                "links": ["https://example.test/x"],
            }
        ],
    }
    payload.update(over)
    return payload


def test_kb_get_and_put_roundtrip(client: TestClient, hooks: Any) -> None:
    assert client.get("/api/kb").json()["experiences"] == []
    response = client.put("/api/kb", json=kb_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["skills"] == ["Python", "SQL"] and body["experiences"][0]["location"] is None
    assert body["experiences"][0]["bullets"] == ["Shipped a thing"] and body["source"] == "resume"
    assert hooks.saved[-1].experiences[0].id == "acme-intern"
    again = client.get("/api/kb").json()
    assert (
        again["experiences"][0]["title"] == "Product Intern" and again["meta"]["shadowed"] is False
    )
    assert client.put("/api/kb", json=again).status_code == 200  # GET -> PUT (meta ignored)


def test_kb_source_is_kept_consistent(client: TestClient, hooks: Any) -> None:
    client.put("/api/kb", json=kb_payload(source="none"))
    assert hooks.saved[-1].source == "resume"
    client.put("/api/kb", json={"experiences": []})
    assert hooks.saved[-1].source == "none"


def test_kb_validation_errors_are_422_and_nothing_is_saved(client: TestClient, hooks: Any) -> None:
    exp = kb_payload()["experiences"][0]

    def errors(payload: dict[str, Any]) -> set[tuple[Any, ...]]:
        response = client.put("/api/kb", json=payload)
        assert response.status_code == 422, payload
        return {tuple(e["loc"]) for e in response.json()["errors"]}

    assert errors({"skills": []}) == {("experiences",)}  # replacement needs the experiences key
    assert errors({"experiances": []}) >= {("experiances",)}
    assert errors({"experiences": "x"}) == {("experiences",)}
    assert errors({"experiences": [{**exp, "id": "a b"}]}) == {("experiences", 0, "id")}
    assert errors({"experiences": [exp, {**exp, "id": "ACME-INTERN"}]}) == {
        ("experiences", 1, "id")
    }
    assert errors({"experiences": [{**exp, "title": " "}]}) == {("experiences", 0, "title")}
    assert errors({"experiences": [{**exp, "start": "June 2025"}]}) == {("experiences", 0, "start")}
    assert errors({"experiences": [{**exp, "end": "2025"}]}) == {("experiences", 0, "end")}
    assert errors({"experiences": [{**exp, "kind": "hobby"}]}) == {("experiences", 0, "kind")}
    assert errors({"experiences": [{**exp, "links": ["javascript:alert(1)"]}]}) == {
        ("experiences", 0, "links")
    }
    assert errors({"experiences": [{**exp, "bullet": ["typo"]}]}) == {("experiences", 0, "bullet")}
    assert errors({"experiences": [{**exp, "bullets": ["x" * 1001]}]}) == {
        ("experiences", 0, "bullets")
    }
    assert errors({"experiences": [5]}) == {("experiences", 0)}
    assert hooks.saved == []


def test_kb_meta_warns_when_experience_files_shadow_it(client: TestClient, paths: AppPaths) -> None:
    (paths.experiences_dir / "acme.md").write_text("---\nid: a\n---\n- x\n", encoding="utf-8")
    meta = client.get("/api/kb").json()["meta"]
    assert meta["shadowed"] is True and meta["experience_files"] == ["acme.md"] and meta["warnings"]
    assert client.put("/api/kb", json=kb_payload()).json()["meta"]["shadowed"] is True


def test_kb_from_resume_proposes_without_saving(
    client: TestClient, hooks: Any, make_ready: Any
) -> None:
    make_ready()
    hooks.proposed = KnowledgeBase(
        source="resume", experiences=[Experience(id="e1", title="Intern", organization="Acme")]
    )
    response = client.post("/api/kb/from-resume")
    assert response.status_code == 200
    body = response.json()
    assert (
        body["proposed"] is True
        and body["saved"] is False
        and body["kb"]["experiences"][0]["id"] == "e1"
    )
    assert hooks.saved == [] and len(hooks.build_calls) == 1
    assert hooks.build_calls[0][0].name == "resume.pdf"


def test_kb_from_resume_preconditions(
    client: TestClient, runtime: DashboardRuntime, hooks: Any, make_ready: Any
) -> None:
    response = client.post("/api/kb/from-resume")
    assert response.status_code == 409 and response.json()["code"] == "resume_missing"
    make_ready()
    runtime.llm_factory = lambda config: None
    response = client.post("/api/kb/from-resume")
    assert response.status_code == 409 and response.json()["code"] == "llm_unavailable"
    assert "set_openai_key.ps1" in response.json()["message"] and hooks.build_calls == []


def test_kb_from_resume_llm_error_is_a_clean_502(
    client: TestClient, hooks: Any, make_ready: Any
) -> None:
    make_ready()
    hooks.build_error = LLMError(f"boom with {FICTIONAL_KEY} and Bearer abcdef123456\nsecond line")
    response = client.post("/api/kb/from-resume")
    assert response.status_code == 502 and response.json()["code"] == "llm_error"
    assert FICTIONAL_KEY not in response.text and "abcdef123456" not in response.text
    assert "Traceback" not in response.text and "second line" not in response.text


def test_kb_from_resume_unreadable_resume_is_422(
    client: TestClient, hooks: Any, make_ready: Any
) -> None:
    make_ready()
    hooks.build_error = ValueError("PDF is encrypted")
    response = client.post("/api/kb/from-resume")
    assert response.status_code == 422 and response.json()["code"] == "resume_unreadable"
    assert "encrypted" in response.json()["message"]


def _fake_brain_exists() -> bool:
    import importlib.util

    return importlib.util.find_spec("autoapply.testing.fake_llm") is not None


def test_default_llm_factory_needs_a_key(runtime: DashboardRuntime) -> None:
    runtime.llm_factory = None
    config = runtime.read_config()
    runtime.env = {}
    assert runtime.make_llm(config) is None
    runtime.env = {"OPENAI_API_KEY": FICTIONAL_KEY}
    assert runtime.make_llm(config) is not None
    runtime.env = {"AUTOAPPLY_TESTING": "1", "AUTOAPPLY_FAKE_LLM": "1"}
    with pytest.raises(LLMError) if not _fake_brain_exists() else contextlib.nullcontext():
        runtime.make_llm(config)


# ------------------------------------------------------------------------------------------ real collaborators


def make_pdf(path: Path, lines: list[str]) -> None:
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path))
    y = 800
    for line in lines:
        pdf.drawString(72, y, line)
        y -= 18
    pdf.save()


def test_lazy_default_hooks_work_against_the_real_modules(
    paths: AppPaths, repo: Any, controller: Any
) -> None:
    from autoapply.dashboard.app import create_app

    runtime = DashboardRuntime(
        paths=paths,
        repo=repo,
        controller=controller,
        env={},
        llm_factory=lambda config: (
            FakeLLM()
        ),  # no scripted purposes -> LLMError -> heuristic parser
    )
    make_pdf(
        paths.resume_file,
        [
            "Ada Testperson",
            "EXPERIENCE",
            "Product Intern, Acme Robotics    June 2025 - August 2025",
            "- Built a dashboard used by 40 analysts",
            "SKILLS",
            "Python, SQL",
        ],
    )
    with TestClient(create_app(runtime), base_url=BASE_URL) as client:
        client.headers["X-CSRF-Token"] = client.get("/api/csrf").json()["csrf_token"]
        empty = client.get("/api/kb").json()
        assert empty["experiences"] == [] and empty["source"] == "none"
        proposal = client.post("/api/kb/from-resume")
        assert proposal.status_code == 200 and proposal.json()["saved"] is False
        assert not paths.knowledge_base_file.exists()
        saved = client.put("/api/kb", json=kb_payload())
        assert saved.status_code == 200 and paths.knowledge_base_file.is_file()
        assert client.get("/api/kb").json()["experiences"][0]["id"] == "acme-intern"


def test_lazy_workbook_inspector_gets_the_config(
    paths: AppPaths, repo: Any, controller: Any
) -> None:
    import openpyxl

    from autoapply.dashboard.app import create_app

    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Verified Opportunities"
    sheet.append(["Company", "Role", "Link", "Location", "Term", "Status"])
    sheet.append(
        [
            "Acme Robotics",
            "Product Intern",
            "https://jobs.acme.example.test/1",
            "Austin, TX",
            "Summer 2027",
            "Open",
        ]
    )
    target = paths.root / "my workbook.xlsx"
    book.save(target)
    runtime = DashboardRuntime(paths=paths, repo=repo, controller=controller, env={})
    with TestClient(create_app(runtime), base_url=BASE_URL) as client:
        client.headers["X-CSRF-Token"] = client.get("/api/csrf").json()["csrf_token"]
        client.put("/api/settings", json={"workbook": {"path": str(target)}})
        report = client.post("/api/workbook/inspect").json()
        assert report["sheet"] == "Verified Opportunities" and report["header_row"] == 1
        assert report["mapping"]["company"] == "Company" and report["data_rows"] == 1


# ------------------------------------------------------------------------------------------ workbook (stubs)


def test_workbook_inspect_paths_and_errors(
    client: TestClient, hooks: Any, paths: AppPaths, tmp_path: Path
) -> None:
    def code(body: Any = None) -> tuple[int, str]:
        response = client.post("/api/workbook/inspect", json=body)
        return response.status_code, response.json().get("code", "")

    assert code() == (422, "workbook_path_missing")
    assert code({"path": "   "}) == (422, "workbook_path_missing")
    assert code({"path": str(tmp_path / "missing.xlsx")}) == (422, "workbook_missing")
    (tmp_path / "notes.txt").write_text("x")
    assert code({"path": str(tmp_path / "notes.txt")}) == (422, "workbook_not_xlsx")
    assert code({"path": str(tmp_path)}) == (422, "workbook_not_xlsx")
    assert client.post("/api/workbook/inspect", json={"path": 5}).status_code == 422
    assert hooks.inspect_calls == []
    book = tmp_path / "book with spaces.xlsx"
    book.write_bytes(b"PK")
    response = client.post("/api/workbook/inspect", json={"path": str(book)})
    assert response.status_code == 200 and response.json() == {
        "sheet": "Verified Opportunities",
        "kept": 3,
    }
    assert hooks.inspect_calls == [book]
    client.put("/api/settings", json={"workbook": {"path": str(book)}})
    assert (
        client.post("/api/workbook/inspect").status_code == 200
    )  # defaults to config.workbook.path
    assert hooks.inspect_calls[-1] == book


def test_workbook_inspect_result_conversion_and_failures(
    client: TestClient, hooks: Any, tmp_path: Path
) -> None:
    from pydantic import BaseModel

    class Report(BaseModel):
        sheet: str = "S"

    class Legacy:
        def to_dict(self) -> dict[str, Any]:
            return {"legacy": True}

    book = tmp_path / "b.xlsx"
    book.write_bytes(b"PK")
    for result, expected in (
        (Report(), {"sheet": "S"}),
        (Legacy(), {"legacy": True}),
        (42, {"report": "42"}),
    ):
        hooks.inspect_result = result
        assert client.post("/api/workbook/inspect", json={"path": str(book)}).json() == expected
    hooks.inspect_error = ValueError(f"cannot read {FICTIONAL_KEY}")
    response = client.post("/api/workbook/inspect", json={"path": str(book)})
    assert response.status_code == 422 and response.json()["code"] == "workbook_unreadable"
    assert FICTIONAL_KEY not in response.text
    hooks.inspect_error = PermissionError("denied")
    assert client.post("/api/workbook/inspect", json={"path": str(book)}).status_code == 422


# ------------------------------------------------------------------------------------------ files


@pytest.fixture
def served(paths: AppPaths) -> AppPaths:
    (paths.artifacts_dir / "7").mkdir(parents=True)
    (paths.artifacts_dir / "7" / "shot.png").write_bytes(b"\x89PNG data")
    (paths.artifacts_dir / "7" / "trace.zip").write_bytes(b"PK zip")
    (paths.artifacts_dir / "7" / "page.html").write_text("<script>alert(1)</script>")
    (paths.artifacts_dir / "7" / "notes.txt").write_text("hello")
    (paths.artifacts_dir / "7" / "data.json").write_text("{}")
    (paths.artifacts_dir / "7" / "run.exe").write_bytes(b"MZ")
    (paths.artifacts_dir / "7" / "sp ace ü.pdf").write_bytes(b"%PDF-1.4")
    (paths.documents_dir / "opp1").mkdir(parents=True)
    (paths.documents_dir / "opp1" / "resume.pdf").write_bytes(PDF_BYTES)
    (paths.root / "secret.txt").write_text("top secret")
    (paths.root / "config.json").write_text("{}")
    return paths


def test_files_are_served_with_safe_headers(client: TestClient, served: AppPaths) -> None:
    pdf = client.get("/files/documents/opp1/resume.pdf")
    assert pdf.status_code == 200 and pdf.content == PDF_BYTES
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"].startswith("inline")
    assert pdf.headers["x-content-type-options"] == "nosniff"
    png = client.get("/files/artifacts/7/shot.png")
    assert png.headers["content-type"] == "image/png" and "content-disposition" in png.headers
    assert (
        client.get("/files/artifacts/7/notes.txt").headers["content-type"].startswith("text/plain")
    )
    assert client.get("/files/artifacts/7/data.json").headers["content-type"] == "application/json"
    assert client.get("/files/artifacts/7/sp%20ace%20%C3%BC.pdf").status_code == 200


@pytest.mark.parametrize("name", ["page.html", "trace.zip"])
def test_active_content_is_only_served_as_a_text_attachment(
    client: TestClient, served: AppPaths, name: str
) -> None:
    response = client.get(f"/files/artifacts/7/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "url",
    [
        "/files/artifacts/7/run.exe",  # extension not allowed
        "/files/artifacts/7",  # directory
        "/files/artifacts/",  # directory listing
        "/files/artifacts/7/missing.png",
        "/files/other/7/shot.png",  # unknown kind
        "/files/artifacts/%2e%2e/secret.txt",
        "/files/artifacts/%2e%2e/config.json",
        "/files/artifacts/7/%2e%2e/%2e%2e/secret.txt",
        "/files/artifacts/..%2fsecret.txt",
        "/files/artifacts/7/..%2f..%2fsecret.txt",
        "/files/artifacts/7%2f..%2f..%2fsecret.txt",
        "/files/artifacts/7/..%5c..%5csecret.txt",
        "/files/artifacts/%2fetc%2fpasswd",
        "/files/artifacts//etc/passwd",
        "/files/artifacts/7//shot.png",
        "/files/artifacts/7/./shot.png".replace("/./", "/%2e/"),
        "/files/artifacts/C:/Windows/win.ini",
        "/files/artifacts/7/shot.png%00.txt",
        "/files/artifacts/7/CON.txt",
        "/files/artifacts/7/shot.png.",
        "/files/artifacts/7/shot.png%20",
        "/files/documents/opp1/resume.pdf/..",
        "/files/artifacts/" + "a" * 5000 + ".png",
    ],
)
def test_traversal_and_bad_paths_are_404(client: TestClient, served: AppPaths, url: str) -> None:
    response = client.get(url)
    assert response.status_code == 404, url
    assert "top secret" not in response.text


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_symlinks_are_never_followed(client: TestClient, served: AppPaths, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "loot.png").write_bytes(b"loot")
    try:
        (served.artifacts_dir / "link.png").symlink_to(outside / "loot.png")
        (served.artifacts_dir / "linkdir").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlinks here")
    assert client.get("/files/artifacts/link.png").status_code == 404
    assert client.get("/files/artifacts/linkdir/loot.png").status_code == 404


def test_resolver_unit_rules(served: AppPaths) -> None:
    assert resolve_served_file(served, "artifacts", "7/shot.png") is not None
    for raw in (
        "",
        "/7/shot.png",
        "7/../7/shot.png",
        "7\\shot.png",
        "../x.png",
        "7/shot.png/",
        "C:/x.png",
    ):
        assert resolve_served_file(served, "artifacts", raw) is None, raw
    assert resolve_served_file(served, "config", "7/shot.png") is None


def test_served_file_ref_maps_stored_paths(served: AppPaths) -> None:
    root = served.root
    assert served_file_ref(served, str(root / "artifacts" / "7" / "shot.png")) == (
        "artifacts",
        "7/shot.png",
    )
    assert served_file_ref(served, "artifacts/7/shot.png") == ("artifacts", "7/shot.png")
    assert served_file_ref(served, "documents/opp1/resume.pdf") == ("documents", "opp1/resume.pdf")
    assert served_file_ref(served, str(root / "secret.txt")) is None
    assert served_file_ref(served, "artifacts/../secret.txt") is None
    assert served_file_ref(served, "") is None and served_file_ref(served, "\x00") is None


def test_files_json_dump_of_config_is_not_reachable(client: TestClient, served: AppPaths) -> None:
    for kind in ("documents", "artifacts"):
        assert client.get(f"/files/{kind}/config.json").status_code == 404
    assert json.loads((served.root / "config.json").read_text()) == {}
