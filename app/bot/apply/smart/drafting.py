"""LLM answers for questions no stored fact settles — accepted only with evidence.

One LLM call per form answers every open question as JSON. Each answer must
carry a verbatim quote from the candidate record; the code (not the prompt)
then checks:
  * the quote really appears in the record,
  * option questions got one of the form's own options,
  * every number in the answer exists in the record or the job posting,
  * any "worked/interned at X" names an organization from the record.
An answer failing any check is discarded — the question is held for the user,
never submitted with unverified text.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from bot.apply.smart.candidate import Candidate, normalize, number_values
from bot.apply.smart.questions import Question
from bot.apply.smart.resolve import pick_option

logger = logging.getLogger(__name__)

Generate = Callable[[str], str]

_MOTIVATION_CUES = ("why ", "why do", "why are", "interest", "excite", "motivat",
                    "what draws", "want to work", "want to join", "passion")
_PAST_WORK = re.compile(
    r"\b(?:worked|interned|was employed|served|consulted)\s+(?:at|with|for)\s+"
    r"([A-Z][\w&.'\-]*(?:\s+(?:[A-Z][\w&.'\-]*|of|and|&))*)")
_DASHES = str.maketrans({"—": ", ", "–": "-"})

_PROMPT = """You fill in a job application form on behalf of a real candidate.
The answers are submitted without further review, so they must be true.

RULES
1. Use ONLY the CANDIDATE RECORD. Never invent experience, employers, numbers,
   dates, skills, opinions, or personal stories.
2. For every answer, "evidence" is a VERBATIM quote (3-30 words, copied exactly)
   from the CANDIDATE RECORD that supports it.
3. If the record does not answer a question, return "status": "insufficient"
   with an empty answer. Returning insufficient is always acceptable.
4. Questions with OPTIONS: "answer" must be exactly one of the options.
5. "Why this company / role" questions: answer only from the candidate's own
   motivation statement in the record, tied to specifics in the JOB POSTING.
   No flattery, no claims of having used the company's products.
6. Free text: first person, plain and specific, no em dashes, within the
   word or character limit given for that question.

CANDIDATE RECORD
{record}

JOB POSTING ({company}, {title})
{posting}

QUESTIONS
{questions}

Return ONLY a JSON object: {{"<id>": {{"answer": "...", "evidence": "...", "status": "answered" | "insufficient"}}, ...}}
"""


@dataclass(frozen=True)
class Draft:
    qid: str
    value: str | None
    evidence: str = ""
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.value is not None and not self.problems


def is_motivation(q: Question) -> bool:
    hay = f" {normalize(q.label)} "
    return any(cue in hay for cue in _MOTIVATION_CUES)


def word_limit(q: Question) -> int:
    return 150 if q.kind == "textarea" else 40


def verify_text(text: str, cand: Candidate, posting: str) -> list[str]:
    """Fabrication checks shared by answers and cover letters."""
    problems = []
    allowed_numbers = cand.numbers() | number_values(posting)
    unknown = sorted(n for n in number_values(text) if n not in allowed_numbers)
    if unknown:
        problems.append(f"numbers not in your record: {', '.join(unknown)}")
    orgs = cand.organizations()
    for match in _PAST_WORK.finditer(text):
        name = normalize(match.group(1))
        if not any(name in org or org in name for org in orgs):
            problems.append(f"claims work at {match.group(1)!r}, which is not in your record")
    if re.search(r"\[[^\]]{2,40}\]", text):
        problems.append("contains a [placeholder]")
    return problems


def _trim(text: str, q: Question) -> str:
    words = text.split()
    if len(words) > word_limit(q) + 20:
        text = " ".join(words[: word_limit(q) + 20])
    if q.max_length and len(text) > q.max_length:
        cut = text[: q.max_length]
        text = cut[: cut.rfind(" ")] if " " in cut else cut
    return text.strip()


def _question_block(questions: list[Question]) -> str:
    items = []
    for q in questions:
        item = {"id": q.qid, "question": q.label, "type": q.kind}
        if q.options:
            item["options"] = list(q.options)
        item["limit"] = (f"{q.max_length} characters" if q.max_length
                         else f"{word_limit(q)} words")
        items.append(item)
    return json.dumps(items, indent=1)


def _parse_json(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _check(q: Question, item: dict, cand: Candidate, corpus: str, posting: str) -> Draft:
    if not isinstance(item, dict) or item.get("status") != "answered":
        return Draft(q.qid, None, problems=("the record doesn't answer this",))
    answer = str(item.get("answer") or "").translate(_DASHES).strip()
    evidence = str(item.get("evidence") or "").strip()
    if not answer:
        return Draft(q.qid, None, problems=("empty answer",))
    problems = []
    ev = normalize(evidence)
    if len(ev.split()) < 3 or ev not in corpus:
        problems.append("evidence quote not found in your record")
    if q.options:
        picked = pick_option(q.options, answer)
        if picked is None:
            problems.append(f"answer {answer!r} is not one of the form's options")
        answer = picked or answer
    else:
        answer = _trim(answer, q)
        problems.extend(verify_text(answer, cand, posting))
    return Draft(q.qid, answer, evidence, tuple(problems))


def draft_answers(questions: list[Question], cand: Candidate, generate: Generate,
                  company: str, title: str, posting: str) -> dict[str, Draft]:
    """Draft and verify answers for ``questions``; one LLM call for the batch."""
    drafts: dict[str, Draft] = {}
    askable = []
    for q in questions:
        if is_motivation(q) and not cand.facts.motivation.strip():
            drafts[q.qid] = Draft(q.qid, None, problems=(
                "motivation question — add your own words under 'motivation' in candidate.yaml",))
        else:
            askable.append(q)
    if not askable:
        return drafts
    prompt = _PROMPT.format(
        record="\n".join(cand.fact_lines()), company=company or "the employer",
        title=title or "this role", posting=(posting or "(not available)")[:6000],
        questions=_question_block(askable))
    try:
        data = _parse_json(generate(prompt))
    except Exception as exc:  # network / quota / provider error
        logger.warning("Answer drafting failed: %s", exc)
        data = {}
    corpus = cand.corpus()
    for q in askable:
        drafts[q.qid] = _check(q, data.get(q.qid), cand, corpus, posting)
        if drafts[q.qid].problems:
            logger.info("Draft rejected for %r: %s", q.label, "; ".join(drafts[q.qid].problems))
    return drafts


def make_generate(llm_config) -> Generate:
    """prompt -> text through the app's configured LLM provider."""
    from core.ai_engine import invoke_llm

    def generate(prompt: str) -> str:
        return invoke_llm(prompt, llm_config, timeout_seconds=90)

    return generate
