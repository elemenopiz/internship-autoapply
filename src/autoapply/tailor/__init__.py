"""Grounded document tailoring (docs/SPEC.md section 5.5): knowledge base, validators, generation, rendering.

Public API:

* ``load_kb(paths)`` / ``save_kb(paths, kb)`` / ``build_kb_from_resume(pdf_path, llm)``: the user's real
  background (``knowledge.py`` documents the experience-file formats);
* ``generate_documents(opportunity, kb, profile, paths, llm, resume_fallback)``: a tailored one-page resume and
  a cover letter under ``documents/<opportunity id>/``, or the user's own PDF when there is no usable KB;
* ``validate_bullet`` / ``validate_cover_letter``: the grounding validators (``grounding.py``).

Invented employers, titles, dates, schools, metrics and skills cannot reach a document: the LLM only returns a
plan that the renderer fills from the KB by id, and every generated sentence is validated (see ``generate.py``).
"""

from autoapply.tailor.generate import TailoringError, generate_documents
from autoapply.tailor.grounding import (
    LetterGrounder,
    filter_cover_letter,
    validate_bullet,
    validate_cover_letter,
)
from autoapply.tailor.knowledge import (
    ResumeExtractionError,
    build_kb_from_resume,
    load_kb,
    load_kb_with_issues,
    save_kb,
)

__all__ = [
    "LetterGrounder",
    "ResumeExtractionError",
    "TailoringError",
    "build_kb_from_resume",
    "filter_cover_letter",
    "generate_documents",
    "load_kb",
    "load_kb_with_issues",
    "save_kb",
    "validate_bullet",
    "validate_cover_letter",
]
