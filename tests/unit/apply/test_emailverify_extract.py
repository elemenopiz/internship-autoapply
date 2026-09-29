"""Pure link/code extraction from realistic verification mails (Workday / Greenhouse / Lever style)."""

from __future__ import annotations

import email
import random
from email.message import EmailMessage, Message

import pytest

from autoapply.apply.emailverify import extract_code, extract_verification_link

WD_LINK = (
    "https://acme.wd5.myworkdayjobs.com/en-US/External/verifyEmail/Zx81QdLm?redirect=%2Fjob%2F7"
)


def build(
    *,
    subject: str = "Verify your account",
    plain: str | None = None,
    html: str | None = None,
    plain_cte: str | None = None,
    html_cte: str | None = None,
    sender: str = "Acme Talent <no-reply@acme.example.test>",
) -> Message:
    """A real RFC-822 message: serialised and parsed back, so transfer encodings are exercised."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "alex.rivera@example.test"
    msg["Subject"] = subject
    if plain is not None:
        msg.set_content(plain, cte=plain_cte or "7bit")
        if html is not None:
            msg.add_alternative(html, subtype="html", cte=html_cte or "7bit")
    elif html is not None:
        msg.set_content(html, subtype="html", cte=html_cte or "7bit")
    return email.message_from_bytes(msg.as_bytes())


WORKDAY_HTML = f"""<!DOCTYPE html><html><head><title>Verify your account</title>
<style>.btn {{ background:#0875e1; }} a[href*="unsub"] {{ color:#999 }}</style></head>
<body><table width="600"><tr><td><a href="https://www.acme.example.test/"><img src="https://cdn.acme.example.test/acme-logo.png" alt="Acme logo"></a></td></tr>
<tr><td><p>Hello Alex,</p>
<p>Thank you for creating a candidate account at Acme. Please verify your email address to finish setting up your account.</p>
<p><a class="btn" href="{WD_LINK.replace("&", "&amp;")}" style="padding:12px">Verify Email</a></p>
<p>If the button does not work, paste this link into your browser: {WD_LINK}</p>
<p style="font-size:11px"><a href="https://www.acme.example.test/privacy">Privacy Policy</a> |
<a href="https://www.acme.example.test/terms">Terms of Use</a> |
<a href="https://mail.acme.example.test/unsubscribe?u=abc123">Unsubscribe</a></p></td></tr></table></body></html>"""

WORKDAY_PLAIN = f"""Hello Alex,

Thank you for creating a candidate account at Acme. Please verify your email address to finish setting up your account.

Verify Email: {WD_LINK}

Privacy Policy: https://www.acme.example.test/privacy
Unsubscribe: https://mail.acme.example.test/unsubscribe?u=abc123
"""


def test_workday_multipart_alternative_quoted_printable() -> None:
    msg = build(
        plain=WORKDAY_PLAIN,
        html=WORKDAY_HTML,
        plain_cte="quoted-printable",
        html_cte="quoted-printable",
    )
    assert "quoted-printable" in msg.as_string()
    assert extract_verification_link(msg) == WD_LINK


def test_workday_html_only_base64() -> None:
    msg = build(html=WORKDAY_HTML, html_cte="base64")
    assert "base64" in msg.as_string()
    assert extract_verification_link(msg) == WD_LINK


def test_plain_text_only_with_the_link_on_its_own_line() -> None:
    msg = build(
        plain=f"Please click the link below to confirm your email address:\n\n{WD_LINK}\n\nThanks,\nAcme"
    )
    assert extract_verification_link(msg) == WD_LINK


def test_angle_bracket_and_trailing_punctuation_in_plain_text() -> None:
    msg = build(
        plain="To activate your account visit <https://acme.example.test/activate/9f8e7d>.\nBye"
    )
    assert extract_verification_link(msg) == "https://acme.example.test/activate/9f8e7d"
    msg = build(plain="Confirm here (https://acme.example.test/c/abc123).")
    assert extract_verification_link(msg) == "https://acme.example.test/c/abc123"


LEVER_HTML = """<html><body>
<div style="display:none">Confirm your email to finish your application</div>
<p>Hi Alex, thanks for applying to Acme Robotics via Lever.</p>
<p><a href="https://email.mailer.example.test/c/eyJ1IjoiaHR0cHM6Ly9qb2JzLmxldmVyLmNvL2FjbWUvY29uZmlybSJ9?utm_source=lever&amp;utm_medium=email">Confirm your email address</a></p>
<p><a href="https://email.mailer.example.test/c/unsub-1234">Unsubscribe</a></p>
<img src="https://email.mailer.example.test/o/pixel.gif" width="1" height="1"></body></html>"""


def test_lever_html_only_with_opaque_click_tracker_uses_the_button_text() -> None:
    msg = build(subject="Confirm your email", html=LEVER_HTML)
    assert extract_verification_link(msg) == (
        "https://email.mailer.example.test/c/eyJ1IjoiaHR0cHM6Ly9qb2JzLmxldmVyLmNvL2FjbWUvY29uZmlybSJ9"
        "?utm_source=lever&utm_medium=email"
    )


def test_tracking_redirect_is_recognised_by_its_wrapped_target() -> None:
    wrapped = (
        "https://click.mailer.example.test/track?u=https%3A%2F%2Facme.wd5.myworkdayjobs.com%2F"
        "External%2Factivate%2FQ1w2E3&e=42"
    )
    html = f'<p>Welcome!</p><a href="{wrapped}">Click here</a> <a href="https://x.example.test/help">Help</a>'
    assert extract_verification_link(build(html=html)) == wrapped


def test_tracking_redirect_wrapping_an_unsubscribe_page_is_ignored() -> None:
    wrapped = "https://click.mailer.example.test/t?u=https%3A%2F%2Facme.example.test%2Funsubscribe%3Ftoken%3Dabc"
    assert extract_verification_link(build(html=f'<a href="{wrapped}">Manage</a>')) is None


def test_button_is_an_image_with_alt_text() -> None:
    html = '<a href="https://acme.example.test/go/abc"><img src="https://cdn.example.test/b.png" alt="Verify my email"></a>'
    assert extract_verification_link(build(html=html)) == "https://acme.example.test/go/abc"


def test_hint_in_the_paragraph_before_a_generic_button() -> None:
    html = "<p>Please verify your email address to continue.</p><p><a href='https://acme.example.test/x/1a2b'>Get started</a></p>"
    assert extract_verification_link(build(html=html)) == "https://acme.example.test/x/1a2b"


def test_token_in_the_url_is_enough() -> None:
    html = '<a href="https://acme.example.test/r?token=9aF3xQ">Go</a>'
    assert extract_verification_link(build(html=html)) == "https://acme.example.test/r?token=9aF3xQ"


def test_entities_and_line_breaks_inside_href_are_cleaned() -> None:
    html = '<a href="https://acme.example.test/verify?a=1&amp;b=2\n   &amp;c=3">Verify</a>'
    assert (
        extract_verification_link(build(html=html))
        == "https://acme.example.test/verify?a=1&b=2&c=3"
    )


def test_best_scoring_link_wins_over_an_earlier_weaker_one() -> None:
    html = (
        '<a href="https://acme.example.test/careers?token=aaa">Careers</a>'
        '<a href="https://acme.example.test/a/b">Confirm your email</a>'
    )
    assert extract_verification_link(build(html=html)) == "https://acme.example.test/a/b"


def test_custom_hints_extend_the_vocabulary() -> None:
    html = '<a href="https://acme.example.test/welcome/abc">Continue</a>'
    msg = build(html=html)
    assert extract_verification_link(msg) is None
    assert (
        extract_verification_link(msg, hints=["Welcome"]) == "https://acme.example.test/welcome/abc"
    )
    assert extract_verification_link(msg, hints=(" ", "")) is None


@pytest.mark.parametrize(
    "html",
    [
        '<a href="https://acme.example.test/unsubscribe?token=1">Confirm unsubscribe</a>',
        '<a href="https://acme.example.test/privacy/verify">Privacy</a>',
        '<a href="https://acme.example.test/terms-of-use">Verify our terms</a>',
        '<a href="https://acme.example.test/images/logo.png">Confirm</a>',
        '<a href="https://acme.example.test/x/verify.jpg">Verify</a>',
        '<a href="https://acme.example.test/"><img alt="Acme logo" src="l.png"></a>',
        '<a href="mailto:hr@acme.example.test?subject=verify">Verify</a>',
        '<a href="javascript:verify()">Verify</a>',
        '<a href="tel:+15125550100">Confirm</a>',
        '<a href="/relative/verify">Verify</a>',
        "<a>Verify</a>",
        '<a href="https://acme.example.test/careers">See open roles</a>',
        '<a href="https://acme.example.test/prefs/preferences?token=1">Email preferences</a>',
        '<a href="https://acme.example.test/v">View in browser</a> Please confirm.',
    ],
)
def test_junk_links_are_never_returned(html: str) -> None:
    assert extract_verification_link(build(html=html)) is None


def test_marketing_mail_without_a_verification_link() -> None:
    plain = "Hi! New internships at Acme.\nBrowse: https://acme.example.test/careers\nUnsubscribe: https://acme.example.test/unsubscribe"
    assert extract_verification_link(build(plain=plain)) is None


def test_attachments_and_empty_messages_yield_nothing() -> None:
    msg = EmailMessage()
    msg["Subject"] = "x"
    msg.set_content("nothing here")
    msg.add_attachment(
        b"https://acme.example.test/verify/abc", maintype="text", subtype="plain", filename="a.txt"
    )
    parsed = email.message_from_bytes(msg.as_bytes())
    assert extract_verification_link(parsed) is None
    assert extract_code(parsed) is None
    assert extract_verification_link(Message()) is None
    assert extract_code(Message()) is None


def test_unknown_charset_and_latin1_bodies_do_not_crash() -> None:
    raw = (
        b"From: a@example.test\r\nTo: b@example.test\r\nSubject: s\r\n"
        b"Content-Type: text/plain; charset=x-nonexistent\r\nContent-Transfer-Encoding: 8bit\r\n\r\n"
        b"Please verify: https://acme.example.test/verify/caf\xe9\r\n"
    )
    assert (
        extract_verification_link(email.message_from_bytes(raw))
        == "https://acme.example.test/verify/caf�"
    )


# ------------------------------------------------------------------------------------------ codes

GREENHOUSE_HTML = """<html><body><p>Hi Alex,</p>
<p>Copy and paste this code into the security code field on your application:</p>
<h2 style="letter-spacing:2px">K7f2QxPd</h2>
<p>After you enter the code, resubmit your application.</p>
<p>This code will expire in 10 minutes. Sent 2026-09-29 by Acme Robotics, 500 Congress Ave, Austin TX 78701.</p></body></html>"""


def test_greenhouse_style_alphanumeric_security_code() -> None:
    plain = "Copy and paste this code into the security code field on your application:\n\nK7f2QxPd\n\nAfter you enter the code, resubmit."
    assert (
        extract_code(
            build(
                subject="Security code for your application to Acme",
                plain=plain,
                html=GREENHOUSE_HTML,
            )
        )
        == "K7f2QxPd"
    )
    assert extract_code(build(html=GREENHOUSE_HTML, html_cte="quoted-printable")) == "K7f2QxPd"


def test_greenhouse_style_code_without_digits_but_mixed_case() -> None:
    plain = "Enter this security code:\nKpFhDtRe\nThanks"
    assert extract_code(build(plain=plain)) == "KpFhDtRe"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Your verification code is 482913", "482913"),
        ("Your verification code is: 4821.", "4821"),
        ("Verification code: 90210777", "90210777"),
        ("OTP: 552901", "552901"),
        ("Use this one-time passcode to sign in:\n\n736 204\n\nIt expires soon.", "736204"),
        ("Your code is 731-946", "731946"),
        ("529183 is your Acme verification code", "529183"),
        (
            "Enter the following code:\r\n\r\n829104\r\n\r\nThis code expires in 10 minutes.",
            "829104",
        ),
        ("Code\n61 pieces\n774411", "774411"),
        ("Your code is 123456 (order 4839201)", "123456"),
        ("Verification code for your Summer 2027 application: 483920", "483920"),
        ("Hello, your login number is 654321.", "654321"),  # standalone 6-digit fallback
    ],
)
def test_code_formats(text: str, expected: str | None) -> None:
    assert extract_code(build(plain=text)) == expected


def test_a_year_like_number_is_only_used_when_nothing_better_exists() -> None:
    assert extract_code(build(plain="Your code is 2027")) == "2027"


@pytest.mark.parametrize(
    "text",
    [
        "Reference number 20481932 for your application.",
        "Call us at 512-555-0142 or +1 512 555 0142.",
        "Austin, TX 78701. Job ID #123456. Price $123456. Ratio 1.123456",
        "Visit https://acme.example.test/t/123456 or https://acme.example.test/verify?code=445566",
        "Your code will arrive shortly.",
        "This code expires in 10 minutes.",
        "Contact hr123456@example.test",
        "",
    ],
)
def test_no_code_in_ordinary_numbers(text: str) -> None:
    assert extract_code(build(plain=text)) is None


def test_code_in_html_only_table_layout() -> None:
    html = "<table><tr><td>Verification code</td></tr><tr><td style='font-size:32px;letter-spacing:8px'>&nbsp;904&#8203;117&nbsp;</td></tr></table><p>Do not share it.</p>"
    assert extract_code(build(html=html)) == "904117"


def test_code_prefers_plain_text_cue_over_html_standalone() -> None:
    msg = build(
        plain="Your verification code is 111222",
        html="<p>Ref 333444</p><p>Your verification code is 111222</p>",
    )
    assert extract_code(msg) == "111222"


# ------------------------------------------------------------------------------------------ robustness


def test_random_html_soup_never_raises_and_results_are_well_formed() -> None:
    rng = random.Random(20260929)
    pieces = [
        "<a href='https://acme.example.test/verify/1'>",
        "</a>",
        "<a>",
        "<p>",
        "</div>",
        "<br/>",
        "<script>",
        "</script>",
        "<img alt='Verify'",
        "<![CDATA[",
        "<!--",
        "-->",
        "&amp;",
        "&#x41;",
        "code 123456",
        "Verify",
        "https://x.test/a b",
        "\x00",
        "<title>",
        "<a href=",
        '"',
        "'",
        ">",
        "<",
        "0123456789",
        "Security code:\n",
        "K7f2QxPd\n",
    ]
    for _ in range(300):
        html = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 40)))
        msg = build(html=html)
        link = extract_verification_link(msg)
        code = extract_code(msg)
        assert link is None or link.startswith(("http://", "https://"))
        assert code is None or code.isalnum()
