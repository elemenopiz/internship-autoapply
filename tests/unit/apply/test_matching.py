from __future__ import annotations

import pytest

from autoapply.apply.matching import (
    best_option,
    is_decline_option,
    is_placeholder_option,
    option_polarity,
    parse_bool,
)
from autoapply.models import DECLINE


@pytest.mark.parametrize(
    "text",
    [
        "Decline To Self Identify",
        "Decline to self-identify",
        "I don't wish to answer",
        "I do not want to answer",
        "I do not wish to self-identify",
        "Prefer not to say",
        "I choose not to disclose",
        "Decline to answer",
        "Rather not say",
    ],
)
def test_decline_wordings_recognised(text: str) -> None:
    assert is_decline_option(text)


@pytest.mark.parametrize("text", ["Male", "No, I do not have a disability", "Asian", "Yes"])
def test_non_decline_not_flagged(text: str) -> None:
    assert not is_decline_option(text)


def test_decline_sentinel_selects_site_specific_wording() -> None:
    options = ["Male", "Female", "Non-binary", "Decline To Self Identify"]
    assert best_option(DECLINE, options) == "Decline To Self Identify"
    vets = ["I am a protected veteran", "I am not a protected veteran", "I don't wish to answer"]
    assert best_option(DECLINE, vets) == "I don't wish to answer"
    assert best_option(DECLINE, ["Yes", "No"]) is None  # never guess a decline that does not exist


def test_boolean_polarity_ignores_decline_and_long_options() -> None:
    disability = [
        "Yes, I have a disability",
        "No, I do not have a disability",
        "I do not want to answer",
    ]
    assert best_option(False, disability) == "No, I do not have a disability"
    assert best_option("Yes", disability) == "Yes, I have a disability"
    assert best_option(True, ["Yes", "No"]) == "Yes"
    assert best_option(False, ["Select...", "Yes", "No"]) == "No"
    assert best_option(True, ["I will", "I will not"]) is None  # no explicit yes/no -> do not guess


def test_string_matching_exact_normalised_and_fuzzy() -> None:
    races = [
        "Hispanic or Latino",
        "White",
        "Black or African American",
        "Asian",
        "Decline to self-identify",
    ]
    assert best_option("asian", races) == "Asian"
    assert best_option("Black or African-American", races) == "Black or African American"
    vets = ["I am a protected veteran", "I am not a protected veteran", "I don't wish to answer"]
    assert best_option("Not a protected veteran", vets) == "I am not a protected veteran"
    assert best_option("Klingon", races) is None
    assert best_option(None, races) is None
    assert best_option("Asian", []) is None


def test_referral_synonyms_and_other_fallback() -> None:
    options = ["Job Board", "Careers Page", "Referral", "Other"]
    assert best_option("Company website", options, intent="referral_source") == "Careers Page"
    assert (
        best_option("Company website", ["LinkedIn", "Other"], intent="referral_source") == "Other"
    )
    assert best_option("Company website", ["LinkedIn", "Indeed"], intent="referral_source") is None


def test_helpers() -> None:
    assert parse_bool("Yes") is True and parse_bool("n") is False and parse_bool("maybe") is None
    assert option_polarity("No, I do not") is False and option_polarity("Not applicable") is None
    assert is_placeholder_option("Please select")
    assert is_placeholder_option("Select...") and is_placeholder_option("")
