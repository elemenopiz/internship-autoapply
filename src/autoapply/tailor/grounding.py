"""Grounding validators: nothing generated may say more than the knowledge base already says.

docs/SPEC.md section 1 rule 1 and section 5.5. This module is the last line of defence between an LLM plan and
a PDF. It never *repairs* text; it only answers "is this supported?" and lets callers revert to the source
bullet or drop the sentence. Every check errs on the side of rejecting.

Checks for a rephrased resume bullet (``validate_bullet``; ``sources`` are the KB bullets it rewrites):

* numbers, percentages, currency amounts, multipliers ("3x") and number words ("three", "doubled") must occur
  in the source bullets, compared by value ("5k" == "$5,000" only when the source says so; "30 percent" == "30%");
* capitalised entities (employers, schools, products, people) and known tech / tool / skill terms must occur in
  the knowledge base (or in ``extra_allowed_text`` when the caller wants a narrower scope, e.g. the owning
  experience only). The job description is deliberately NOT part of the allowed text for resume bullets: a
  posting that mentions Kubernetes must never let a bullet claim Kubernetes;
* at least ``MIN_BULLET_OVERLAP`` (70%) of the rephrasing's content words (stop words removed, light stemming,
  the leading action verb exempt so "Built" may become "Developed") must come from the source bullets;
* length limits, no placeholders ("[Company]"), at most ``MAX_REPHRASE_SOURCES`` sources.

Checks for cover-letter sentences (``validate_cover_letter``): every sentence is checked separately.

* numbers must occur in the evidence experiences, the profile facts or the opportunity's title/term/location;
* capitalised entities must occur in the evidence, the KB skills, the profile or the opportunity's company /
  title / location / term. Employers of experiences that are NOT cited as evidence are therefore rejected;
* known tech terms must occur in the evidence, the KB skills or the profile (a term that only the job
  description mentions is rejected in claims; it is tolerated inside a sentence that merely names the role);
* a sentence that claims something about the applicant's past ("I built ...", "my experience ...") must have at
  least ``MIN_CLAIM_OVERLAP`` (50%) of its content words supported by the evidence / profile / role names
  (generic cover-letter vocabulary counts as supported; action verbs do not);
* placeholders such as "[Your Name]" are rejected.

Known limits (documented, not hidden): a purely qualitative claim that contains no number, capitalised name,
tech term or first-person claim marker cannot be verified by text analysis. The structural defences (renderer
takes employers, titles, dates and schools from the KB by id; skills are copied from the KB; the letter falls
back to a template built only from KB text) cover those cases.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, get_origin

from pydantic import BaseModel, ConfigDict, model_validator

from autoapply.models import Experience, GroundingReport, KnowledgeBase, Opportunity, Profile
from autoapply.normalize import norm_text, parse_year_month

MIN_BULLET_OVERLAP = 0.7
MIN_CLAIM_OVERLAP = 0.5
MAX_NOVEL_WORDS = 3
MAX_REPHRASE_SOURCES = 3
MIN_BULLET_WORDS = 3
MAX_BULLET_CHARS = 350
MAX_VIOLATIONS = 30

# --------------------------------------------------------------------------------------------- vocabularies


def _words(text: str) -> list[str]:
    return text.split()


STOPWORDS = frozenset(
    _words("""a about above after again against all also am an and any are as at be because been before being
    below between both but by can could did do does doing down during each few for from further had has have
    having he her here hers herself him himself his how i if in into is it its itself just me more most my
    myself no nor not of off on once only or other our ours ourselves out over own same she should so some such
    than that the their theirs them themselves then there these they this those through to too under until up
    very was we were what when where which while who whom why will with would you your yours yourself
    yourselves s t ll ve re d m across among per via within without upon""")
)

_ACTION_VERBS = frozenset(
    _words("""achieve administer advise allocate analyze analyse apply architect assemble assess assist
audit author automate balance benchmark boost brainstorm budget build calculate capture catalog champion
clarify coach collaborate collect communicate compile complete conceive conduct configure consolidate
construct consult contribute control convert coordinate create curate customize debug decrease define
deliver demonstrate deploy design detect determine develop devise diagnose direct discover document draft
drive earn edit educate elevate eliminate embed emphasize enable encourage engage engineer enhance ensure
establish estimate evaluate examine execute expand expedite experiment explore extract facilitate finalize
forecast formulate found generate grow guide handle help identify illustrate implement improve increase
influence inform initiate innovate inspect install instruct integrate interpret interview introduce
investigate launch lead learn leverage maintain manage map market master maximize measure mediate mentor
migrate minimize model moderate monitor motivate navigate negotiate network normalize observe obtain onboard
operate optimize orchestrate organize oversee own partner perform pilot pioneer plan present prioritize
process procure produce program promote propose prototype provide publish purchase query raise rank rebuild
recommend reconcile record recruit redesign reduce refactor refine register reinforce render reorganize
replace report represent research resolve restructure retain retrieve review revise revamp run sample save
scale schedule score screen secure select serve set shape share ship simplify simulate solve source
spearhead standardize steer streamline strengthen structure study submit supervise support surpass survey
synthesize systematize tailor teach test track train transform translate troubleshoot tutor uncover unify
update upgrade utilize validate verify visualize volunteer win write built led ran wrote won made drove took
grew taught spoke sold held began chose gave met kept brought found got saw did had was were became sent
spent thought understood oversaw undertook rebuilt cut put""")
)

# Everyday resume words a rephrasing may use even if its source bullet does not: structure words, neutral nouns,
# adjectives and adverbs. Outcome nouns (revenue, sales, savings, growth, efficiency, satisfaction, ...) and
# names are deliberately absent: adding one is adding a claim, so it must come from the source.
_NEUTRAL_WORDS = frozenset(
    _words("""team teams project projects process processes system systems data dataset datasets report reports
    reporting tool tools model models plan plans program programs event events group groups page pages app
    apps application applications service services platform workflow workflows pipeline pipelines database
    schedule schedules budget meeting meetings presentation presentations workshop workshops session sessions
    guide guides documentation checklist checklists template templates survey surveys feedback request
    requests ticket tickets issue issues task tasks goal goals target metric metrics dashboard dashboards
    analysis analytics insight insights finding findings recommendation recommendations result results output
    quality time week weeks month months year years day days hour hours minute minutes cycle cycles quarter
    semester term course courses class classes student students faculty staff employee employees customer
    customers client clients user users stakeholder stakeholders manager managers leader leaders member
    members partner partners peer peers vendor vendors department departments division unit units region
    regions market industry company organization organizations university campus club community product
    products feature features release releases launch version prototype design code software network
    environment operations logistics supply delivery deliveries route routes shipment shipments inventory
    order orders invoice payment role roles responsibility responsibilities skill skills knowledge
    experience approach method methods step steps phase phases stage stages milestone milestones deadline
    deadlines requirement requirements specification specifications standard standards guideline guidelines
    policy policies procedure procedures practice practices technique techniques resource resources material
    materials content document documents file files record records list lists table tables chart charts
    graph graphs summary summaries overview brief email emails message messages call calls interview
    interviews candidate candidates applicant applicants position positions opportunity opportunities
    weekly monthly daily annual yearly quarterly regional national global internal external cross
    functional new existing key main primary multiple several various large small complex detailed step
    automated manual custom real live online remote onsite local main core essential critical common
    consistent accurate timely clear concise effective structured shared central direct end full part
    initial final first second third next previous current future recent early late high low top best
    better more most less least many few each every all both own other another different same similar
    related relevant specific general overall individual collaborative independent independently jointly
    together directly closely regularly consistently successfully effectively quickly efficiently
    accurately clearly carefully actively also then while during after before across among between within
    without through throughout using via based per including includes included like such as well
    company's team's business businesses academic student-led hands-on""")
)
_LETTER_WORDS = frozenset(
    _words("""excited eager opportunity opportunities role roles position positions internship internships
intern interns team teams skills skill strong passion passionate interest interested contribute contribution
contributions learn learning growth mission values collaborate collaboration experience experiences
background motivated ability abilities apply applying application resume attached thank thanks consideration
time forward discuss candidacy qualifications student studying pursuing degree university summer hands-on
real-world problem solving analytical communication leadership detail oriented fast quickly environment
company organization organizations work working impact meaningful challenge challenges professional career
further hope believe confident well suited fit prepared ready look conversation chance
sincerely regards dear hiring manager recruiter committee please feel free contact available start date
enthusiasm curiosity dedication commitment reliable adaptable learner collaborative curious results goals
objectives success successful part join joining 
using use used uses tools technologies technology data business product products customers clients
stakeholders projects project tasks responsibilities responsibility first next additionally moreover overall
outstanding great good excellent practical academic coursework courses course
knowledge understanding foundation foundations approach ideas idea solutions solution decisions decision
insights insight processes process operations operational strategy strategic analysis analyses analytics
teamwork independently independent effectively efficient efficiently 
future goal aspire aspiring aspirations pathway path field industry industries world""")
)

_COMMON_STARTERS = frozenset(
    _words("""thank thanks please however therefore furthermore additionally moreover overall
specifically beyond outside whether given since throughout although though while during after before because
if when where what why how who which both each every many most some such also with through by for from
to of on in at as it its this that these those there here now today currently recently previously
originally ultimately finally first second third next then later earlier meanwhile besides instead
otherwise still yet again once twice always never often sometimes usually generally particularly especially
mainly primarily largely together alongside along among between within without across around about above
below under over into onto upon toward towards until unless like unlike despite regarding concerning
excited eager passionate enthusiastic thrilled delighted honored grateful confident committed motivated
interested curious driven dedicated detail-oriented""")
)

_CONNECTORS = frozenset(
    {"of", "at", "and", "for", "the", "de", "la", "del", "van", "von", "der", "da", "di"}
)
_HARMLESS_ACRONYMS = frozenset({"gpa", "us", "usa", "cv"})
_PRONOUN_FORMS = frozenset({"i", "i'm", "i've", "i'll", "i'd", "i’m", "i’ve", "i’ll", "i’d"})


def _stem_word(word: str) -> str:
    """Very light stemmer so "managed", "manages", "managing" and "manager" agree (lower-case input)."""
    if word.isdigit() or len(word) <= 3 or not word.isalpha():
        return word
    if len(word) > 4 and word.endswith(("ies", "ied")):
        word = word[:-3] + "y"
    elif word.endswith(("sses", "xes", "zes", "ches", "shes")):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "is", "us")):
        word = word[:-1]
    for suffix in ("ing", "ment", "ion", "er", "ed", "ly"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    if word.endswith("e") and len(word) > 4:
        word = word[:-1]
    return word


def stem(word: str) -> str:
    """Public alias of the light stemmer (lower-case input expected)."""
    return _stem_word(word)


_ACTION_STEMS = frozenset(_stem_word(w) for w in _ACTION_VERBS)
_SAFE_STEMS = _ACTION_STEMS | frozenset(_stem_word(w) for w in _NEUTRAL_WORDS)
_LETTER_STEMS = frozenset(_stem_word(w) for w in _LETTER_WORDS)
_COMMON_WORDS = STOPWORDS | _COMMON_STARTERS | _ACTION_VERBS

# --------------------------------------------------------------------------------------------- numbers

_NUMBER_WORDS: dict[str, str] = {
    "zero": "0",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
    "thirty": "30",
    "forty": "40",
    "fifty": "50",
    "sixty": "60",
    "seventy": "70",
    "eighty": "80",
    "ninety": "90",
    "hundred": "100",
    "hundreds": "100s",
    "thousand": "1000",
    "thousands": "1000s",
    "million": "1000000",
    "millions": "1000000s",
    "billion": "1000000000",
    "billions": "1000000000s",
    "dozen": "12",
    "dozens": "12s",
    "half": "0.5",
    "double": "2x",
    "doubled": "2x",
    "doubling": "2x",
    "twice": "2x",
    "triple": "3x",
    "tripled": "3x",
    "tripling": "3x",
    "thrice": "3x",
    "quadruple": "4x",
    "quadrupled": "4x",
}
_NUMBER_WORD_RE = re.compile(
    r"(?<![A-Za-z])(" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")(?![A-Za-z])",
    re.IGNORECASE,
)
_ONE_RE = re.compile(r"(?<![A-Za-z])one(?![A-Za-z])", re.IGNORECASE)
_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?P<cur>[$€£¥])?\s?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"(?P<suf>\s?%|\s?(?:percent|per\s?cent)\b|[kKmMbB]\b|[xX]\b|\+|(?:st|nd|rd|th)\b"
    r"|\s(?:dollars?|usd)\b)?"
    r"(?![A-Za-z0-9])"
)
_MULTIPLIERS = {"k": Decimal(1000), "m": Decimal(1000000), "b": Decimal(1000000000)}


def _format_decimal(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text[:-2] if text.endswith(".0") else text


def _number_matches(text: str, include_one: bool) -> list[tuple[int, int, str]]:
    """(start, end, canonical token) of every number in ``text``, in order of appearance."""
    found: list[tuple[int, int, str]] = []
    for match in _NUM_RE.finditer(text):
        try:
            value = Decimal(match.group("num").replace(",", ""))
        except InvalidOperation:  # pragma: no cover - the regex only admits valid decimals
            continue
        suffix = re.sub(r"\s+", "", (match.group("suf") or "")).lower()
        currency = match.group("cur") or ""
        unit = ""
        if suffix.startswith(("%", "percent")):
            unit = "%"
        elif suffix in _MULTIPLIERS:
            value *= _MULTIPLIERS[suffix]
        elif suffix == "x":
            unit = "x"
        elif suffix == "+":
            unit = "+"
        elif suffix in {"dollar", "dollars", "usd"}:
            currency = "$"
        found.append((match.start(), match.end(), f"{currency}{_format_decimal(value)}{unit}"))
    for match in _NUMBER_WORD_RE.finditer(text):
        found.append((match.start(), match.end(), _NUMBER_WORDS[match.group(1).lower()]))
    if include_one:
        found.extend((m.start(), m.end(), "1") for m in _ONE_RE.finditer(text))
    return sorted(found)


def numeric_tokens(text: str, *, include_one: bool = True) -> list[str]:
    """Canonical numeric tokens in ``text``, in order of appearance (duplicates kept).

    Digits: "1,200" -> "1200", "$5k" -> "$5000", "30 percent" -> "30%", "3x" -> "3x", "10th" -> "10",
    "500+" -> "500+". Digits glued to letters ("S3", "Q3", "5G") are identifiers, not numbers. Number words
    ("three", "doubled", "half", "dozen") map to the same canonical form as their digits. ``include_one``
    controls whether the word "one" counts (it is ordinary prose in letters but a metric in a bullet).
    """
    return [token for _, _, token in _number_matches(text, include_one)]


def _following_words(text: str, end: int, limit: int = 3) -> tuple[str, ...]:
    """Stems of the next ``limit`` content words after ``end`` in the same clause (stop words skipped)."""
    words: list[str] = []
    position = end
    while len(words) < limit:
        match = re.match(r"[\s\-\u2013]*([A-Za-z]+)", text[position:])
        if not match:
            break
        position += match.end()
        word = match.group(1).lower()
        if word not in STOPWORDS:
            words.append(_stem_word(word))
    return tuple(words)


def numeric_contexts(text: str, *, include_one: bool = True) -> list[tuple[str, tuple[str, ...]]]:
    """(canonical number, stems of the content words that follow it) for every number in ``text``.

    "five interns to plan" -> ("5", ("intern", "plan")); "by 12%." -> ("12%", ()). Used to check that a
    number keeps its unit: "five years" must not be justified by "a team of five interns".
    """
    return [
        (token, _following_words(text, end)) for _, end, token in _number_matches(text, include_one)
    ]


# --------------------------------------------------------------------------------------------- tech lexicon

# Words that are ordinary English unless written as the product ("Excel", "Go", "Swift"): they only count as
# tech terms in generated text when capitalised. Single letters keep their case ("R", "C").
_AMBIGUOUS_NAMES = frozenset(
    _words("""excel go swift rust ruby spark express notion slack oracle julia linear unity lean r c workday intel
    uber stripe adobe amplitude sketch confluence sap airflow jenkins flask rails bootstrap cassandra agile
    waterfall azure lambda snowflake redshift bash dart sass""")
)
_UPPER_NAMES = frozenset({"r", "c", "sap"})
# "R" / "C" alone are only the languages after "in", "with", "using", "and", "or", ":" or ", ".
_LANGUAGE_CONTEXT = (
    r"(?:(?<=\bin )|(?<=\bwith )|(?<=\busing )|(?<=\band )|(?<=\bor )|(?<=, )|(?<=: ))"
)

# "name" or "name=regex". Names are lower case; spaces in a name match any run of space / . - _ (or none).
_TECH_TERMS = r"""python, java, javascript, typescript, c++=c\+\+, c#=c\#, f#=f\#, php, kotlin, scala,
perl, matlab, sql, nosql, html, css, sass, bash, powershell, vba, dart, haskell, lua, verilog, vhdl, sas,
stata, spss, golang, objective-c=objective[\s\-]*c, react, react native, angular, vue, svelte, next.js,
node.js, django, flask, fastapi, spring boot, .net=\.net, rails, laravel, jquery, bootstrap, tailwind,
pandas, numpy, scipy, matplotlib, seaborn, plotly, scikit-learn, tensorflow, pytorch, keras, opencv,
pyspark, hadoop, kafka, airflow, dbt, jupyter, streamlit, flutter, aws, azure, gcp, google cloud, docker,
kubernetes, terraform, ansible, jenkins, github actions, ci/cd=ci\s*/\s*cd, git, github, gitlab,
bitbucket, linux, unix, nginx, lambda, ec2, s3, redshift, bigquery, snowflake, databricks, mysql,
postgresql, postgres, sqlite, mongodb, redis, dynamodb, cassandra, elasticsearch, firebase, sql server,
tableau, power bi, looker, qlik, google sheets, google analytics, mixpanel, amplitude,
a/b testing=a\s*/\s*b[\s\-]*test(?:ing|s)?, machine learning, deep learning, nlp, computer vision,
data mining, large language model, llm, generative ai, jira, confluence, asana, trello, figma, sketch,
miro, monday.com=monday\.com, productboard, pendo, smartsheet, ms project, microsoft project, salesforce,
hubspot, sap, netsuite, servicenow, workday, microsoft dynamics, quickbooks, bloomberg, factset,
capital iq, pitchbook, alteryx, uipath, zapier, power automate, power apps, powerpoint, microsoft office,
google workspace, sharepoint, visio, autocad, solidworks, labview, arduino, raspberry pi, agile, scrum,
kanban, six sigma, lean six sigma, waterfall, devops, design thinking, okrs, excel, go, swift, rust,
ruby, spark, express, notion, slack, oracle, julia, linear, unity, lean, r, c, google, alphabet, facebook,
microsoft, netflix, tesla, spacex, openai, anthropic, nvidia, intel, ibm, adobe, uber, lyft, airbnb,
stripe, palantir, twitter, linkedin, snapchat, dropbox, goldman sachs, morgan stanley, jpmorgan,
j.p. morgan=j\.?\s?p\.?[\s\-]*morgan, bank of america, citigroup, wells fargo, mckinsey, bain, bcg,
boston consulting group, deloitte, pwc, ernst & young=ernst\s*(?:&|and)\s*young, kpmg, accenture,
capital one, blackrock, walmart, procter & gamble=procter\s*(?:&|and)\s*gamble, mckesson, boeing,
lockheed martin, raytheon, northrop grumman, general electric,
johnson & johnson=johnson\s*(?:&|and)\s*johnson, pfizer, harvard, stanford, yale, princeton, cornell,
carnegie mellon, berkeley"""


def _term_body(name: str) -> str:
    parts = [part for part in re.split(r"[\s.\-_]+", name) if part]
    return r"[\s.\-_]*".join(re.escape(part) for part in parts)


def _compile_terms() -> list[tuple[str, re.Pattern[str], re.Pattern[str]]]:
    """(canonical name, pattern for generated text, pattern for the allowed corpus) per term."""
    compiled: list[tuple[str, re.Pattern[str], re.Pattern[str]]] = []
    seen: set[str] = set()
    for entry in (e.strip() for e in _TECH_TERMS.replace("\n", " ").split(",")):
        name, _, override = entry.partition("=")
        name = re.sub(r"\s+", " ", name.strip())
        if not name or name in seen:
            continue
        seen.add(name)
        body = override or _term_body(name)
        loose = re.compile(rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9+#])", re.IGNORECASE)
        if name in _AMBIGUOUS_NAMES:
            surface = name.upper() if name in _UPPER_NAMES else name.capitalize()
            context = _LANGUAGE_CONTEXT if len(name) == 1 else ""
            tight = re.compile(
                rf"(?<![A-Za-z0-9&/]){context}{re.escape(surface)}(?![A-Za-z0-9+#&/'\u2019.\-])"
            )
        else:
            tight = loose
        compiled.append((name, tight, loose))
    return compiled


_TECH_PATTERNS = _compile_terms()


def find_tech_terms(text: str, *, strict: bool = True) -> list[str]:
    """Known tool / language / employer / school terms in ``text`` (sorted canonical names).

    ``strict=True`` is for generated output: ambiguous words ("Excel", "Go", "Swift", "R") only count when
    capitalised as the tool. ``strict=False`` is for the allowed corpus: any casing counts as support.
    """
    text = _strip_accents(text)
    found = {
        name for name, tight, loose in _TECH_PATTERNS if (tight if strict else loose).search(text)
    }
    return sorted(found)


# --------------------------------------------------------------------------------------------- text helpers

_SPACE_RE = re.compile(r"\s+")
_PLACEHOLDER_RE = re.compile(
    r"\[[^\]\n]{1,60}\]|\{\{[^}\n]*\}\}|<[A-Za-z][^>\n]{0,40}>|\b(?:lorem ipsum|TBD)\b|X{3,}"
    r"|\binsert\s+(?:your\s+)?\w+(?:\s+\w+)?\s+here\b",
    re.IGNORECASE,
)
_MARKDOWN_RE = re.compile(r"(\*\*|__|`{1,3}|^#{1,6}\s+|^\s*[-*+•]\s+)", re.MULTILINE)
_NUMERIC_TOKEN_RE = re.compile(r"^\d[\d,.]*[kKmMbBxX]?$")
_TOKEN_RE = re.compile(r"[^\W_](?:[\w'’&]*[^\W_])?", re.UNICODE)
_ABBREVIATIONS = frozenset(
    _words(
        "mr mrs ms dr prof sr jr st vs etc inc ltd co corp e.g i.e u.s no approx dept univ fig gov mt ft "
        "sept jan feb mar apr jun jul aug oct nov dec"
    )
)


def clean_text(text: str) -> str:
    """Canonical form of generated text: NFKC (full-width letters and ligatures fold to plain ones), invisible
    format characters (zero-width spaces, soft hyphens, bidi marks) and control characters removed, whitespace
    collapsed. Validation runs on this form, and it is what gets rendered, so a look-alike or an invisible
    character cannot smuggle a term past the checks and still show up in the PDF."""
    text = unicodedata.normalize("NFKC", text)
    kept = (
        " " if c.isspace() else c
        for c in text
        if unicodedata.category(c) not in {"Cc", "Cf"} or c.isspace()
    )
    return _SPACE_RE.sub(" ", "".join(kept)).strip()


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def sanitize_generated_text(text: str) -> str:
    """Strip markdown emphasis / list markers and control characters from LLM output (paragraph-wise)."""
    lines = [_MARKDOWN_RE.sub("", line) for line in text.replace("\r\n", "\n").split("\n")]
    return "\n".join(clean_text(line) for line in lines).strip()


def has_placeholder(text: str) -> bool:
    """True for "[Your Name]", "{{company}}", "<insert>", "TBD", "Lorem ipsum" ..."""
    return bool(_PLACEHOLDER_RE.search(text))


def split_sentences(text: str) -> list[str]:
    """Split ``text`` into sentences (newlines always split; abbreviations and initials do not)."""
    sentences: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        line = clean_text(line)
        if not line:
            continue
        start = 0
        for match in re.finditer(r"[.!?]+[\"')\]]*\s+", line):
            end = match.end()
            head = line[start : match.start() + 1].rstrip(".!?\"')]")
            last = head.split(" ")[-1].lower() if head else ""
            following = line[end : end + 1]
            if last.rstrip(".") in _ABBREVIATIONS or (len(last) == 1 and last.isalpha()):
                continue
            if following and not (
                following.isupper() or following.isdigit() or following in "\"'(["
            ):
                continue
            sentences.append(line[start:end].strip())
            start = end
        tail = line[start:].strip()
        if tail:
            sentences.append(tail)
    return sentences


def content_tokens(text: str) -> list[str]:
    """Stemmed content words of ``text`` (stop words and single letters removed, number words -> digits)."""
    tokens: list[str] = []
    for token in norm_text(text).split():
        token = _NUMBER_WORDS.get(token, token)
        if token in STOPWORDS or (len(token) == 1 and not token.isdigit()):
            continue
        tokens.append(_stem_word(token))
    return tokens


def _rephrased_tokens(rephrased: str) -> list[str]:
    """Content words of a rephrasing without its leading action verb (which may legitimately change)."""
    words = clean_text(rephrased).split(" ")
    tokens = content_tokens(rephrased)
    first = norm_text(words[0]).split() if words and words[0] else []
    if tokens and first and tokens[0] in _ACTION_STEMS and first[0] not in STOPWORDS:
        return tokens[
            1:
        ]  # only a known action verb may be swapped freely; anything else must be earned
    return tokens


def token_overlap(rephrased: str, sources: Sequence[str]) -> float:
    """Fraction of ``rephrased``'s content words found in ``sources`` (leading verb exempt), 0.0 - 1.0."""
    tokens = _rephrased_tokens(rephrased)
    if not tokens:
        return 1.0
    known = {t for source in sources for t in content_tokens(source)}
    return sum(1 for t in tokens if t in known) / len(tokens)


def novel_words(rephrased: str, sources: Sequence[str]) -> list[str]:
    """Stemmed content words of ``rephrased`` (leading verb exempt) that no source contains, unique, in order."""
    known = {t for source in sources for t in content_tokens(source)}
    return list(dict.fromkeys(t for t in _rephrased_tokens(rephrased) if t not in known))


# --------------------------------------------------------------------------------------------- entities


def _strip_possessive(token: str) -> str:
    return re.sub(r"['’]s?$", "", token)


def _is_common_word(lower: str) -> bool:
    if lower in _COMMON_WORDS or _stem_word(lower) in _ACTION_STEMS or lower in _LETTER_WORDS:
        return True
    return len(lower) >= 5 and lower.isalpha() and lower.endswith(("ing", "ed", "ly"))


def _is_entity_token(token: str, *, initial: bool) -> bool:
    core = _strip_possessive(token)
    if len(core) < 2 or not any(c.isalpha() for c in core):
        return False
    lower = core.lower()
    if lower in _PRONOUN_FORMS or _NUMERIC_TOKEN_RE.match(core):
        return False
    if not (core[0].isupper() or any(c.isupper() for c in core[1:])):
        return False
    if core.isupper() and lower.replace(".", "") in _HARMLESS_ACRONYMS:
        return False
    return not (initial and core[0].isupper() and core[1:].islower() and _is_common_word(lower))


def _sentence_entities(sentence: str) -> list[str]:
    tokens = list(_TOKEN_RE.finditer(sentence))

    def starts_sentence(i: int) -> bool:
        # the first word, or the word after "." / "!" / "?" (also after an abbreviation such as "Co.")
        return i == 0 or sentence[tokens[i - 1].end() : tokens[i].start()].lstrip().startswith(
            (".", "!", "?")
        )

    candidate = [
        _is_entity_token(m.group(0), initial=starts_sentence(i)) for i, m in enumerate(tokens)
    ]

    def adjacent(a: int, b: int) -> bool:
        return not sentence[tokens[a].end() : tokens[b].start()].strip()

    phrases: list[str] = []
    i = 0
    while i < len(tokens):
        if not candidate[i]:
            i += 1
            continue
        parts = [_strip_possessive(tokens[i].group(0))]
        j = i
        while True:
            if j + 1 < len(tokens) and candidate[j + 1] and adjacent(j, j + 1):
                j += 1
                parts.append(_strip_possessive(tokens[j].group(0)))
            elif (
                j + 2 < len(tokens)
                and tokens[j + 1].group(0) in _CONNECTORS
                and candidate[j + 2]
                and adjacent(j, j + 1)
                and adjacent(j + 1, j + 2)
            ):
                parts += [tokens[j + 1].group(0), _strip_possessive(tokens[j + 2].group(0))]
                j += 2
            else:
                break
        phrases.append(" ".join(parts))
        i = j + 1
    return phrases


def find_entities(text: str) -> list[str]:
    """Capitalised entity phrases ("Acme Robotics", "University of Texas at Austin", "Kubernetes", "SQL").

    Sentence-initial words are entities only when they are not ordinary English (verbs, function words).
    Whitelisted harmless acronyms (GPA, US) and the pronoun "I" are ignored. Order is preserved, duplicates
    are removed.
    """
    seen: dict[str, None] = {}
    for sentence in split_sentences(text):
        for phrase in _sentence_entities(sentence):
            seen.setdefault(phrase)
    return list(seen)


# --------------------------------------------------------------------------------------------- corpora


def date_words(value: str | None) -> str:
    """Words a date renders as: "2028-05" -> "2028-05 may 2028 05/2028 2028"."""
    if not value:
        return ""
    text = value.strip()
    if text.lower() in {"present", "current", "now", "ongoing"}:
        return "present current"
    parsed = parse_year_month(text)
    if not parsed:
        return text
    year, month = parsed
    names = _words(
        "january february march april may june july august september october november december"
    )
    return f"{year:04d}-{month:02d} {names[month - 1]} {names[month - 1][:3]} {year} {month:02d}/{year}"


def experience_text(exp: Experience) -> str:
    """Everything an experience says about itself (used as grounding evidence)."""
    parts = [exp.title, exp.organization or "", exp.location or "", *exp.bullets, *exp.skills]
    parts += [date_words(exp.start), date_words(exp.end)]
    return "\n".join(p for p in parts if p)


def _year(value: str | None) -> str:
    parsed = parse_year_month(value) if value else None
    if parsed:
        return str(parsed[0])
    return value if value and value.isdigit() and len(value) == 4 else ""


def experience_number_text(exp: Experience) -> str:
    """Like ``experience_text`` but without month renderings (a month number must not justify a metric)."""
    parts = [exp.title, exp.organization or "", exp.location or "", *exp.bullets, *exp.skills]
    parts += [_year(exp.start), _year(exp.end)]
    return "\n".join(p for p in parts if p)


def kb_text(kb: KnowledgeBase) -> str:
    """Case-preserving corpus of the whole knowledge base (KB skills + every experience)."""
    return "\n".join([*kb.skills, *(experience_text(e) for e in kb.experiences)])


def profile_text(profile: Profile | None) -> str:
    """Profile facts a letter may legitimately mention (name, education, location)."""
    if profile is None:
        return ""
    parts = [
        profile.first_name,
        profile.last_name,
        profile.preferred_name,
        profile.school,
        profile.degree,
        profile.major,
        profile.minor,
        profile.gpa,
        date_words(profile.education_start_date),
        date_words(profile.graduation_date),
        profile.city,
        profile.state,
    ]
    return "\n".join(p for p in parts if p)


def profile_number_text(profile: Profile | None) -> str:
    """The numbers of the profile a letter may quote: GPA and the years of study."""
    if profile is None:
        return ""
    parts = [profile.gpa, _year(profile.education_start_date), _year(profile.graduation_date)]
    return "\n".join(p for p in parts if p)


def opportunity_text(opportunity: Opportunity) -> str:
    """The parts of an opportunity that name the role (NOT the free-text description)."""
    parts = [
        opportunity.company,
        opportunity.title,
        opportunity.location or "",
        opportunity.term or "",
    ]
    return "\n".join(p for p in parts if p)


def _aliases(raw: str) -> list[str]:
    """Abbreviations of organisation names present in ``raw`` ("Massachusetts Institute of Technology" -> MIT)."""
    aliases: list[str] = []
    for line in raw.split("\n"):
        for phrase in _sentence_entities(line):
            words = [w for w in phrase.split() if w[0].isupper()]
            if len(words) >= 2:
                initials = "".join(w[0] for w in words)
                if 2 <= len(initials) <= 6:
                    aliases.append(initials)
        for match in re.finditer(r"University of ([A-Z][\w]+)(?: at ([A-Z][\w-]+))?", line):
            state, city = match.group(1), match.group(2)
            aliases.append(f"U{state[0]}")
            if city:
                aliases += [f"U{state[0]} {city}", f"U{state[0]}-{city}"]
    return aliases


@dataclass(frozen=True)
class Corpus:
    """Pre-digested allowed text: padded normalised text for phrase lookups, numbers, tech terms, stems."""

    raw: str
    norm: str
    tech: frozenset[str]
    stems: frozenset[str]

    @classmethod
    def of(cls, *texts: str) -> Corpus:
        raw = "\n".join(t for t in texts if t)
        aliases = " ".join(norm_text(a) for a in _aliases(raw))
        return cls(
            raw=raw,
            norm=f" {norm_text(raw)} {aliases} ",
            tech=frozenset(find_tech_terms(raw, strict=False)),
            stems=frozenset(content_tokens(raw)),
        )

    def has_phrase(self, phrase: str) -> bool:
        """True if the (possessive-stripped) phrase occurs on word boundaries.

        "A and B", "A at B" and "A for B" are checked part by part ("Data Analyst at Acme" is two names
        joined by a preposition); connectors that are part of a name ("University of Texas") are not split.
        """
        key = norm_text(_strip_possessive(phrase))
        if not key or f" {key} " in self.norm:
            return True
        parts = re.split(r"\s+(?:and|&|at|for)\s+", phrase)
        return len(parts) > 1 and all(self.has_phrase(p) for p in parts)


def _report(violations: list[str]) -> GroundingReport:
    return GroundingReport(ok=not violations, violations=violations[:MAX_VIOLATIONS])


def _excerpt(text: str, limit: int = 60) -> str:
    text = clean_text(text)
    return text if len(text) <= limit else text[: limit - 3] + "..."


# --------------------------------------------------------------------------------------------- bullets


def validate_bullet(
    rephrased: str,
    sources: Sequence[str],
    kb: KnowledgeBase,
    *,
    extra_allowed_text: str | None = None,
) -> GroundingReport:
    """Check that ``rephrased`` says nothing that the source bullet(s) do not (module docstring).

    ``extra_allowed_text`` narrows the entity / tech-term scope: when given it REPLACES the whole-KB text
    (the sources are always allowed). ``generate.py`` passes the owning experience plus the KB skills so a
    bullet cannot borrow another experience's employer or tools. The returned report has ``ok=False`` and a
    list of human-readable violations; ``replaced_with_source`` is left at 0 for the caller to count.
    """
    text = clean_text(rephrased)
    source_list = [clean_text(s) for s in sources if clean_text(s)]
    violations: list[str] = []
    if not text:
        return _report(["empty rephrasing"])
    if not source_list:
        return _report(["rephrasing cites no source bullet"])
    if len(source_list) > MAX_REPHRASE_SOURCES:
        violations.append(
            f"rephrasing merges {len(source_list)} bullets (max {MAX_REPHRASE_SOURCES})"
        )
    if len(text) > MAX_BULLET_CHARS:
        violations.append(f"rephrasing is {len(text)} characters long (max {MAX_BULLET_CHARS})")
    if len(text.split()) < MIN_BULLET_WORDS:
        violations.append("rephrasing is too short to be a bullet")
    if has_placeholder(text):
        violations.append("rephrasing contains placeholder text")

    joined = "\n".join(source_list)
    source_numbers = set(numeric_tokens(joined))
    violations += [
        f"number {number!r} does not occur in the source bullet(s)"
        for number in dict.fromkeys(numeric_tokens(text))
        if number not in source_numbers
    ]
    allowed = Corpus.of(joined, kb_text(kb) if extra_allowed_text is None else extra_allowed_text)
    violations += [
        f"entity {entity!r} does not occur in the knowledge base"
        for entity in find_entities(text)
        if not allowed.has_phrase(entity)
    ]
    violations += [
        f"tool/skill {term!r} does not occur in the knowledge base"
        for term in find_tech_terms(text)
        if term not in allowed.tech
    ]
    novel = novel_words(text, source_list)
    unfamiliar = [
        w
        for w in novel
        if w not in _SAFE_STEMS and w not in allowed.stems and not any(c.isdigit() for c in w)
    ]
    if unfamiliar:
        violations.append(
            "uses words that neither the source nor everyday resume wording contain: "
            + ", ".join(unfamiliar[:6])
        )
    if len(novel) > MAX_NOVEL_WORDS:
        violations.append(
            f"adds {len(novel)} words the source does not have (maximum {MAX_NOVEL_WORDS}): "
            + ", ".join(novel[:6])
        )
    overlap = token_overlap(text, source_list)
    if overlap < MIN_BULLET_OVERLAP:
        violations.append(
            f"only {overlap:.0%} of the wording comes from the source (minimum {MIN_BULLET_OVERLAP:.0%})"
        )
    return _report(violations)


# --------------------------------------------------------------------------------------------- letters

_MY_CLAIM_RE = re.compile(
    r"\bmy\s+(?:\w+\s+){0,3}(?:experience|experiences|role|roles|work|responsibilities|internship|"
    r"internships|projects?|background|skills?|time|involvement|contributions?|expertise)\b"
    r"|\bI\s+have\s+(?:\w+\s+){0,2}(?:experience|expertise|proficiency|background|skills?|knowledge)\b",
    re.IGNORECASE,
)
_I_VERB_RE = re.compile(
    r"\b(?:I|we)(?:'ve|’ve| have| had)?\s+(?:(?:also|then|successfully|independently|previously|"
    r"recently|quickly)\s+)?(?P<verb>[A-Za-z]+)\b"
)
IRREGULAR_PAST = frozenset(
    _words(
        "built led ran wrote won made drove took grew taught spoke sold held began chose gave met kept "
        "brought found got saw did had was were became sent spent thought understood oversaw undertook "
        "rebuilt cut put"
    )
)


def is_claim(sentence: str) -> bool:
    """True if the sentence asserts something about the applicant's past work ("I built ...", "my experience")."""
    for match in _I_VERB_RE.finditer(sentence):
        verb = match.group("verb").lower()
        if verb.endswith("ed") or verb in IRREGULAR_PAST:
            return True
    return bool(_MY_CLAIM_RE.search(sentence))


@dataclass(frozen=True)
class _Scope:
    corpus: Corpus  # evidence + KB skills + profile + opportunity names: entities
    own_tech: frozenset[str]  # tech terms the applicant's own data mentions (no opportunity text)
    free_numbers: frozenset[
        str
    ]  # numbers of the profile / opportunity / skills: usable in any context
    evidence_next: dict[
        str, frozenset[str]
    ]  # number in the evidence -> stems that follow it there ("" = none)


_ADDRESSEES = "Hiring Manager, Hiring Team, Hiring Committee, Recruiting Team, Talent Acquisition, Human Resources"


class LetterGrounder:
    """Sentence-level grounding for cover letters and other free text about the applicant.

    Build once per (KB, opportunity, profile); ``filter`` / ``check`` accept ``evidence_ids`` to scope the
    experiences a paragraph may draw on (``None`` = every KB experience).
    """

    def __init__(
        self, kb: KnowledgeBase, opportunity: Opportunity, profile: Profile | None = None
    ) -> None:
        self.kb = kb
        self.known_ids = {e.id for e in kb.experiences}
        self._by_id = {e.id: e for e in kb.experiences}
        self._profile = profile_text(profile)
        self._profile_numbers = profile_number_text(profile)
        self._opportunity = opportunity_text(opportunity) + "\n" + _ADDRESSEES
        self._role = Corpus.of(opportunity.company, opportunity.title)
        self._skills = "\n".join(kb.skills)
        self._scopes: dict[tuple[str, ...] | None, _Scope] = {}

    def _scope(self, evidence_ids: Sequence[str] | None) -> _Scope:
        key = None if evidence_ids is None else tuple(sorted(set(evidence_ids) & self.known_ids))
        if key not in self._scopes:
            experiences = self.kb.experiences if key is None else [self._by_id[i] for i in key]
            evidence = "\n".join(experience_text(e) for e in experiences)
            own = "\n".join([evidence, self._skills, self._profile])
            following: dict[str, set[str]] = {}
            numbers_text = "\n".join(experience_number_text(e) for e in experiences)
            for number, words in numeric_contexts(numbers_text):
                following.setdefault(number, set()).update(words or ("",))
            self._scopes[key] = _Scope(
                corpus=Corpus.of(own, self._opportunity),
                own_tech=frozenset(find_tech_terms(own, strict=False)),
                free_numbers=frozenset(
                    numeric_tokens(
                        "\n".join([self._skills, self._profile_numbers, self._opportunity])
                    )
                ),
                evidence_next={n: frozenset(words) for n, words in following.items()},
            )
        return self._scopes[key]

    def check(self, sentence: str, evidence_ids: Sequence[str] | None = None) -> list[str]:
        """Violations of one sentence (empty list = supported)."""
        scope = self._scope(evidence_ids)
        corpus = scope.corpus
        violations: list[str] = []
        if has_placeholder(sentence):
            violations.append("placeholder text")
        claim = is_claim(sentence)
        violations += self._number_problems(sentence, scope)
        violations += [
            f"entity {e!r} is not supported by the evidence, profile or opportunity"
            for e in find_entities(sentence)
            if not corpus.has_phrase(e)
        ]
        for term in find_tech_terms(sentence):
            if term not in scope.own_tech and (claim or term not in self._role.tech):
                violations.append(f"tool/skill {term!r} is not supported by the evidence")
        if claim:
            violations += self._claim_overlap(sentence, corpus)
        return violations

    @staticmethod
    def _number_problems(sentence: str, scope: _Scope) -> list[str]:
        """A number must occur in the evidence AND be used with the same unit word ("five years" != "five interns")."""
        problems: list[str] = []
        for number, words in dict.fromkeys(numeric_contexts(sentence, include_one=False)):
            if number in scope.free_numbers:
                continue
            following = scope.evidence_next.get(number)
            if following is None:
                problems.append(f"number {number!r} is not supported by the evidence")
            elif words and "" not in following and not following.intersection(words):
                problems.append(
                    f"number {number!r} is not used with {'/'.join(words)!r} in the evidence"
                )
        return problems

    def _claim_overlap(self, sentence: str, corpus: Corpus) -> list[str]:
        words = [t for t in content_tokens(sentence) if t not in _LETTER_STEMS or t in corpus.stems]
        if not words:
            return []
        supported = sum(1 for t in words if t in corpus.stems or t in self._role.stems)
        ratio = supported / len(words)
        if ratio < MIN_CLAIM_OVERLAP:
            return [
                f"claim is only {ratio:.0%} supported by the evidence (minimum {MIN_CLAIM_OVERLAP:.0%})"
            ]
        return []

    def filter(
        self, text: str, evidence_ids: Sequence[str] | None = None
    ) -> tuple[list[str], list[str]]:
        """Split ``text`` into (kept sentences, violation messages for the dropped ones)."""
        kept: list[str] = []
        dropped: list[str] = []
        for sentence in split_sentences(sanitize_generated_text(text)):
            problems = self.check(sentence, evidence_ids)
            if problems:
                dropped.append(f"sentence {_excerpt(sentence)!r}: {'; '.join(problems)}")
            else:
                kept.append(sentence)
        return kept, dropped


def validate_cover_letter(
    text: str,
    kb: KnowledgeBase,
    opportunity: Opportunity,
    profile: Profile | None = None,
    *,
    evidence_ids: Sequence[str] | None = None,
) -> GroundingReport:
    """Validate every sentence of ``text`` (module docstring); ``ok`` only if all sentences are supported."""
    grounder = LetterGrounder(kb, opportunity, profile)
    _, dropped = grounder.filter(text, evidence_ids)
    return _report(dropped)


def filter_cover_letter(
    text: str,
    kb: KnowledgeBase,
    opportunity: Opportunity,
    profile: Profile | None = None,
    *,
    evidence_ids: Sequence[str] | None = None,
) -> tuple[str, GroundingReport]:
    """Keep only the supported sentences of ``text``; returns (kept text, report of the dropped ones).

    Reusable by the free-text answer engine (docs/SPEC.md section 6): the returned text is empty when
    nothing survives, and the caller then uses its deterministic template.
    """
    grounder = LetterGrounder(kb, opportunity, profile)
    kept, dropped = grounder.filter(text, evidence_ids)
    return " ".join(kept), _report(dropped)


def unique(items: Iterable[str]) -> list[str]:
    """Order-preserving de-duplication."""
    return list(dict.fromkeys(items))


class LenientModel(BaseModel):
    """Base for schemas the LLM fills in: unknown keys are ignored, missing / null values become empty.

    The JSON schema handed to the provider still lists every field as required (no defaults), which is what
    structured-output modes want; only *validation* of a scripted or sloppy reply is forgiving.
    """

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _fill_missing(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        filled = dict(data)
        for name, field in cls.model_fields.items():
            if filled.get(name) is None:
                filled[name] = [] if get_origin(field.annotation) is list else ""
        return filled
