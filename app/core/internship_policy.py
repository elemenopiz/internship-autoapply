"""Scope and profile gates for unattended 2027 applications.

Target cycles: Summer 2027 internships and co-ops, plus Spring 2027 co-ops.
Role families (the user's intern-list categories): product, data analysis,
business analysis, marketing, consulting/strategy, public sector, supply chain.
"""

from __future__ import annotations

import re
from pathlib import Path


ROLE_TERMS = (
    # product / program / project
    "product", "program manager", "technical program", "project manager",
    "project management",
    # strategy / consulting / operations / business
    "consulting", "consultant", "strategy", "strategic", "operations", "business development",
    "business analyst", "business analytics", "business intelligence",
    # analysis
    "analyst", "analytics", "data analysis", "insights", "health systems optimization",
    # marketing / growth / communications
    "marketing", "brand", "growth", "communications", "social media", "content", "digital",
    "e-commerce", "ecommerce",
    # supply chain
    "supply chain", "supplier", "procurement", "sourcing", "logistics", "planning",
    "purchasing", "inventory",
    # public sector
    "policy", "government", "public sector", "public affairs",
)

#: Titles that are engineering/science roles whatever else they mention
#: ("Growth Engineer", "Data Scientist"): outside the target role families.
EXCLUDED_ROLE_TERMS = (
    "software engineer", "software developer", "engineer intern", "engineering intern",
    "developer", "machine learning", "scientist", "hardware", "firmware", "mechanical",
    "electrical", "civil engineer", "chemical engineer",
    "process engineer", "quantitative trading",
)

_CO_OP = re.compile(r"\bco-?\s?op\b|\bcoop\b")


def is_target_internship(job) -> bool:
    """Summer 2027 (or Spring 2027 co-op) internship in a target role family.

    intern-list.com leads are already filtered to the user's chosen categories
    (and to internships), so their titles are not re-screened for role terms.
    """
    title = job.title.lower()
    text = f"{job.title} {job.description}".lower()
    co_op = bool(_CO_OP.search(text))
    in_cycle = "2027" in text and ("summer" in text or ("spring" in text and co_op))
    if not in_cycle:
        return False
    if getattr(job, "platform", "") == "intern_list":
        return True
    is_internship = "intern" in title or bool(_CO_OP.search(title))
    return (is_internship
            and any(term in title for term in ROLE_TERMS)
            and not any(term in title for term in EXCLUDED_ROLE_TERMS))


def _llm_key_label(provider: str) -> str:
    if provider == "openai":
        return "OpenAI API key (OPENAI_API_KEY)"
    return "LLM provider and API key"


def missing_profile_fields(config) -> list[str]:
    profile = config.profile
    answers = profile.screening_answers
    required = {
        "first name": profile.first_name,
        "last name": profile.last_name,
        "email": profile.email,
        "phone": profile.phone,
        "city": profile.city,
        "state": profile.state,
        "resume file": profile.fallback_resume_path,
        "major": answers.get("major"),
        "graduation month/year": answers.get("graduation_date"),
        "work authorization": answers.get("work_authorization"),
        "visa sponsorship": answers.get("visa_sponsorship"),
        _llm_key_label(config.llm.provider): config.llm.provider and config.llm.api_key,
    }
    missing = [name for name, value in required.items() if not value]
    if profile.fallback_resume_path and not Path(profile.fallback_resume_path).is_file():
        missing.append("resume PDF file")
    return missing
