"""Mock pages that adapters must REFUSE (bot walls, SSO, closed, already applied) or SURVIVE (invisible badge).

Two layers live here:

1. Low-level building blocks shared by ``greenhouse.py`` / ``lever.py`` / ``ashby.py``: HTML helpers,
   ``Origin`` (builds third-party looking URLs on ``*.localhost``), and the CAPTCHA widget kit
   (``captcha_widget`` / ``captcha_overlay`` / ``recaptcha_badge`` / ``install_captcha_routes``).
2. Factories, one per blocker, each returning a ready ``MockSite``::

       captcha_visible(company="acme", provider=None, placement="overlay", base="greenhouse")
       invisible_badge(company="acme")
       cloudflare_wall(company="acme", status=403)
       sso_only(company="acme")
       closed_posting(company="acme", variant="closed"|"filled"|"expired"|"not_found")
       already_applied(company="acme")
       employer_redirect(company="acme", target="workday", target_path="/", hops=1, new_tab=False)
       make_site(kind, company="acme", jobs=None, **options)      # dispatch by factory name

Guarantees (adapter authors: rely on these, and only these)
----------------------------------------------------------
CAPTCHA widgets are rendered like the real products, as iframes whose ``src`` points at the vendor host
(mapped onto the mock server through ``<vendor-host>.localhost``, so ``normalize.host_of(src)`` yields the real
vendor host)::

    hcaptcha   div.h-captcha > iframe[title^="Widget containing checkbox for hCaptcha"]   newassets.hcaptcha.com
               (challenge frame: iframe[title="Main content of the hCaptcha challenge"])
               response fields: textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]
    recaptcha  div.g-recaptcha > iframe[title="reCAPTCHA"]                                  www.google.com/recaptcha/api2/anchor
               (challenge frame: iframe[title="recaptcha challenge expires in two minutes"] .../recaptcha/api2/bframe)
               response field: textarea[name="g-recaptcha-response"]
    turnstile  div.cf-turnstile > iframe[title="Widget containing a Cloudflare security challenge"]  challenges.cloudflare.com
    arkose     div#arkose-enforcement > iframe[title="Verification challenge"]                       client-api.arkoselabs.com

Clicking inside a CAPTCHA frame is a HUMAN-ONLY action. Every solve attempt is recorded in
``site.state["captcha_interactions"]`` (list of dicts) so wave-2 tests can assert an adapter NEVER touched a
challenge; a solved token lands in the parent's hidden response field and in ``site.state["captcha_tokens"]``.

``invisible_badge``  A regular Greenhouse ("new" variant) form that additionally carries the real
    ``div.grecaptcha-badge`` (fixed bottom-right, mostly off-screen, with an iframe titled "reCAPTCHA" whose
    src contains ``size=invisible``). It has NO challenge and MUST stay submittable: a submission is recorded.
``captcha_visible``  A form whose submission requires a solved challenge. ``placement="overlay"`` (default) puts a
    modal ``div#captcha-overlay[role=dialog]`` with the challenge frame over the form, so clicks on the form are
    intercepted; ``placement="inline"`` renders the widget inside the form above the submit button. Either way
    nothing is recorded until a human solves it.
``cloudflare_wall``  EVERY path (GET and POST) answers HTTP ``status`` (403) with the Cloudflare interstitial:
    ``<title>Just a moment...</title>``, text "Verify you are human", ``iframe`` (title "Widget containing a
    Cloudflare security challenge", src on challenges.cloudflare.com), header ``cf-mitigated: challenge``.
``sso_only``  Every page says "Sign in with your company SSO" with a single ``a#sso-login`` (-> /sso/start ->
    /sso/idp). There is NO application form, no guest apply, no email/password field on the portal itself.
``closed_posting``  ``closed``: HTTP 200 "This job is no longer accepting applications"; ``filled``: 200 "The
    position has been filled"; ``expired``: 200 "This posting has expired"; ``not_found``: HTTP 404 "Sorry, we
    couldn't find anything here". No form in any variant.
``already_applied``  HTTP 200, text "You have already applied to this job", no form.
``employer_redirect``  ``/jobs/<id>`` is an employer careers page with ``a#apply-now`` ("Apply now") pointing at
    ``/jobs/<id>/apply`` which answers ``status`` (302) through ``hops`` redirects into another hub site given by
    ``target`` (site name). The target URL is resolved lazily on each request through ``self.hub``; when the
    target is missing the redirector answers 502. ``new_tab=True`` adds ``target="_blank"`` to the link.

Every site honours ``self.faults`` (base middleware) and records nothing but FINAL submissions.
"""

from __future__ import annotations

import html
import json
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from autoapply.testing.mock_ats.base import MockJob, MockSite

Provider = Literal["hcaptcha", "recaptcha", "turnstile", "arkose"]
Placement = Literal["inline", "overlay", "on_submit"]
CookieBanner = Literal["bar", "modal"]

# --------------------------------------------------------------------------------- generic helpers


def esc(value: object) -> str:
    """HTML-escape ``value`` for text and attribute contexts."""
    return html.escape("" if value is None else str(value), quote=True)


def json_for_script(value: Any) -> str:
    """JSON that is safe inside an inline ``<script>`` block."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_document(title: str, body: str, head: str = "", body_attrs: str = "") -> str:
    """A complete HTML document with a viewport, no favicon request and an escaped title."""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<link rel='icon' href='data:,'>"
        f"<title>{esc(title)}</title>{head}</head><body {body_attrs}>{body}</body></html>"
    )


def html_response(
    title: str,
    body: str,
    head: str = "",
    *,
    status: int = 200,
    body_attrs: str = "",
    headers: dict[str, str] | None = None,
) -> HTMLResponse:
    return HTMLResponse(
        render_document(title, body, head, body_attrs), status_code=status, headers=headers
    )


@dataclass(frozen=True)
class Origin:
    """Scheme + port of the request being served, to build ``<vendor-host>.localhost`` URLs."""

    scheme: str
    port: int | None

    @classmethod
    def of(cls, request: Request) -> Origin:
        return cls(request.url.scheme, request.url.port)

    def on(self, host: str) -> str:
        suffix = "" if self.port in (None, 80, 443) else f":{self.port}"
        return f"{self.scheme}://{host}.localhost{suffix}"


def job_or_default(jobs: Sequence[MockJob] | None, default: MockJob) -> list[MockJob]:
    return list(jobs) if jobs else [default]


def deferred_html(inner: str, delay_s: float, mount_id: str = "content-mount") -> str:
    """``inner`` now, or (``delay_s`` > 0) injected by script after the delay with a "Loading" placeholder."""
    if delay_s <= 0:
        return f"<div id='{mount_id}'>{inner}</div>"
    return (
        f"<div id='{mount_id}'><div class='loading' role='status'>Loading...</div></div>"
        f"<template id='{mount_id}-template'>{inner}</template>"
        f"<script>setTimeout(function(){{var t=document.getElementById('{mount_id}-template');"
        f"document.getElementById('{mount_id}').replaceChildren(t.content.cloneNode(true));}},"
        f"{int(delay_s * 1000)});</script>"
    )


_COOKIE_JS = (
    "document.querySelectorAll('#onetrust-accept-btn-handler,#onetrust-reject-all-handler')"
    ".forEach(function(b){b.addEventListener('click',function(){var sdk="
    "document.getElementById('onetrust-consent-sdk');if(sdk){sdk.remove();}"
    "document.cookie='OptanonAlertBoxClosed='+new Date().toISOString()+'; path=/';});});"
)


def cookie_banner(kind: CookieBanner | None) -> str:
    """OneTrust style consent UI: ``bar`` (fixed at the bottom) or ``modal`` (dark backdrop, blocks the page).

    Buttons: ``#onetrust-accept-btn-handler`` "Accept All Cookies", ``#onetrust-reject-all-handler`` "Reject All"
    (both remove the banner) and ``#onetrust-pc-btn-handler`` "Cookie Settings" (does nothing).
    """
    if kind is None:
        return ""
    backdrop = (
        "<div class='onetrust-pc-dark-filter' style='position:fixed;top:0;left:0;right:0;bottom:0;"
        "background:rgba(0,0,0,.5);z-index:2147483644'></div>"
        if kind == "modal"
        else ""
    )
    place = (
        "bottom:0;left:0;right:0"
        if kind == "bar"
        else "top:50%;left:50%;transform:translate(-50%,-50%);width:min(560px,90vw);border-radius:8px"
    )
    button = "padding:8px 16px;margin:0 8px 0 0;font:inherit;cursor:pointer"
    return (
        f"<div id='onetrust-consent-sdk'>{backdrop}"
        "<div id='onetrust-banner-sdk' role='region' aria-label='Cookie banner' "
        f"style='position:fixed;{place};z-index:2147483645;background:#fff;color:#222;padding:18px 24px;"
        "box-shadow:0 0 18px rgba(0,0,0,.3);font-family:Arial,sans-serif;font-size:14px'>"
        "<h2 id='onetrust-policy-title' style='margin:0 0 8px;font-size:16px'>Your Privacy</h2>"
        "<p id='onetrust-policy-text' style='margin:0 0 12px'>We use cookies to improve your experience, "
        "analyse traffic and personalise content. You can change your choice at any time.</p>"
        "<div id='onetrust-button-group'>"
        f"<button type='button' id='onetrust-pc-btn-handler' style='{button}'>Cookie Settings</button>"
        f"<button type='button' id='onetrust-reject-all-handler' style='{button}'>Reject All</button>"
        f"<button type='button' id='onetrust-accept-btn-handler' style='{button}'>Accept All Cookies</button>"
        f"</div></div></div><script>{_COOKIE_JS}</script>"
    )


# --------------------------------------------------------------------------------- CAPTCHA kit

_VENDOR_HOST: dict[str, str] = {
    "hcaptcha": "newassets.hcaptcha.com",
    "recaptcha": "www.google.com",
    "turnstile": "challenges.cloudflare.com",
    "arkose": "client-api.arkoselabs.com",
}
_RESPONSE_FIELD: dict[str, str] = {
    "hcaptcha": "h-captcha-response",
    "recaptcha": "g-recaptcha-response",
    "turnstile": "cf-turnstile-response",
    "arkose": "fc-token",
}
_SITEKEYS: dict[str, str] = {
    "hcaptcha": "10000000-ffff-ffff-ffff-000000000001",
    "recaptcha": "6LeMockMockMockMockMockMockMockMockMock01",
    "turnstile": "0x4AAAAAAAmockmockmock01",
    "arkose": "00000000-0000-0000-0000-000000000042",
}

CAPTCHA_PROVIDERS: tuple[str, ...] = tuple(_VENDOR_HOST)


def captcha_response_field(provider: str) -> str:
    """Name of the hidden response field the solved token is written to."""
    return _RESPONSE_FIELD[provider]


def captcha_interactions(site: MockSite) -> list[dict[str, Any]]:
    """Every attempt (by a human or a misbehaving adapter) to solve a challenge on ``site``."""
    interactions: list[dict[str, Any]] = site.state.setdefault("captcha_interactions", [])
    return interactions


def captcha_token_ok(site: MockSite, token: str | None) -> bool:
    """True when ``token`` was issued by this site's mock challenge (i.e. a human solved it)."""
    return bool(token) and token in site.state.get("captcha_tokens", set())


def frame_url(
    provider: str, origin: Origin, mode: str = "checkbox", widget_id: str = "0a1b2c"
) -> str:
    """URL of the vendor iframe (``mode``: checkbox | challenge | invisible)."""
    base = origin.on(_VENDOR_HOST[provider])
    key = _SITEKEYS[provider]
    if provider == "hcaptcha":
        return (
            f"{base}/captcha/v1/3f2a9c1/static/hcaptcha.html#frame={'challenge' if mode == 'challenge' else 'checkbox'}"
            f"&id={widget_id}&sitekey={key}&theme=light&recaptchacompat=true"
        )
    if provider == "recaptcha":
        if mode == "challenge":
            return f"{base}/recaptcha/api2/bframe?hl=en&v=mock&k={key}"
        size = "invisible" if mode == "invisible" else "normal"
        kind = "enterprise" if mode == "invisible" else "api2"
        return (
            f"{base}/recaptcha/{kind}/anchor?ar=1&k={key}&hl=en&v=mock&size={size}&cb={widget_id}"
        )
    if provider == "turnstile":
        return f"{base}/cdn-cgi/challenge-platform/h/b/turnstile/if/ov2/av0/rcv/kx2f1/{key}/light/fbE/normal/auto/"
    return f"{base}/fc/assets/ec-game-core/game-core/1.17.0/standard/index.html?session={widget_id}"


_IFRAME_TITLE: dict[tuple[str, str], str] = {
    ("hcaptcha", "checkbox"): "Widget containing checkbox for hCaptcha security challenge",
    ("hcaptcha", "challenge"): "Main content of the hCaptcha challenge",
    ("recaptcha", "checkbox"): "reCAPTCHA",
    ("recaptcha", "challenge"): "recaptcha challenge expires in two minutes",
    ("recaptcha", "invisible"): "reCAPTCHA",
    ("turnstile", "checkbox"): "Widget containing a Cloudflare security challenge",
    ("arkose", "challenge"): "Verification challenge",
}
_FRAME_SIZE: dict[tuple[str, str], tuple[int, int]] = {
    ("hcaptcha", "checkbox"): (303, 78),
    ("hcaptcha", "challenge"): (400, 580),
    ("recaptcha", "checkbox"): (304, 78),
    ("recaptcha", "challenge"): (400, 580),
    ("turnstile", "checkbox"): (300, 65),
    ("arkose", "challenge"): (400, 500),
}


def _iframe(provider: str, mode: str, origin: Origin, widget_id: str) -> str:
    title = _IFRAME_TITLE.get((provider, mode), "Verification challenge")
    width, height = _FRAME_SIZE.get((provider, mode), (304, 78))
    src = frame_url(provider, origin, mode, widget_id)
    extra = ""
    if provider == "recaptcha":
        extra = (
            ' role="presentation" name="a-' + widget_id + '" sandbox="allow-forms allow-popups '
            'allow-same-origin allow-scripts allow-top-navigation allow-modals"'
        )
    elif provider == "turnstile":
        extra = (
            f' id="cf-chl-widget-{widget_id}" allow="cross-origin-isolated; fullscreen; autoplay"'
            ' sandbox="allow-same-origin allow-scripts allow-popups"'
        )
    elif provider == "hcaptcha":
        extra = f' data-hcaptcha-widget-id="{widget_id}" data-hcaptcha-response=""'
    style = "border:none;overflow:hidden;" if provider == "turnstile" else "overflow:hidden;"
    return (
        f'<iframe src="{esc(src)}" title="{esc(title)}" width="{width}" height="{height}" '
        f'frameborder="0" scrolling="no" tabindex="0"{extra} '
        f'style="width:{width}px;height:{height}px;{style}"></iframe>'
    )


def captcha_widget(provider: str, origin: Origin, widget_id: str = "0a1b2c") -> str:
    """Inline VISIBLE challenge widget (checkbox style) with its hidden response field(s)."""
    key = _SITEKEYS[provider]
    field = _RESPONSE_FIELD[provider]
    if provider == "hcaptcha":
        return (
            f'<div class="h-captcha" data-sitekey="{key}" data-hcaptcha-widget-id="{widget_id}" '
            f'id="captcha-widget">'
            f"{_iframe('hcaptcha', 'checkbox', origin, widget_id)}"
            f'<textarea id="g-recaptcha-response-{widget_id}" name="g-recaptcha-response" '
            f'style="display:none"></textarea>'
            f'<textarea id="h-captcha-response-{widget_id}" name="h-captcha-response" '
            f'style="display:none"></textarea></div>'
        )
    if provider == "recaptcha":
        return (
            f'<div class="g-recaptcha" data-sitekey="{key}" id="captcha-widget">'
            f'<div style="width:304px;height:78px"><div>{_iframe("recaptcha", "checkbox", origin, widget_id)}</div>'
            f'<textarea id="g-recaptcha-response" name="g-recaptcha-response" '
            f'class="g-recaptcha-response" style="display:none"></textarea></div></div>'
        )
    if provider == "turnstile":
        return (
            f'<div class="cf-turnstile" data-sitekey="{key}" id="captcha-widget"><div>'
            f"{_iframe('turnstile', 'checkbox', origin, widget_id)}"
            f'<input type="hidden" name="{field}" id="cf-chl-widget-{widget_id}_response"></div></div>'
        )
    return (
        f'<div id="arkose-enforcement" data-public-key="{key}"><div id="captcha-widget">'
        f"{_iframe('arkose', 'challenge', origin, widget_id)}"
        f'<input type="hidden" name="{field}" id="fc-token"></div></div>'
    )


def captcha_overlay(provider: str, origin: Origin, widget_id: str = "0a1b2c") -> str:
    """Modal overlay with the challenge frame that covers the whole page until a human solves it."""
    mode = "challenge" if provider in {"hcaptcha", "recaptcha", "arkose"} else "checkbox"
    field = _RESPONSE_FIELD[provider]
    return (
        '<div id="captcha-overlay" class="captcha-overlay" role="dialog" aria-modal="true" '
        'aria-label="Security check" style="position:fixed;top:0;left:0;right:0;bottom:0;'
        "background:rgba(0,0,0,.65);z-index:2147483646;display:flex;align-items:center;"
        'justify-content:center">'
        '<div style="background:#fff;border-radius:8px;padding:20px;max-width:440px;'
        'font-family:Arial,Helvetica,sans-serif;text-align:center">'
        '<h2 style="margin:0 0 6px;font-size:20px">Verify you are human</h2>'
        '<p style="margin:0 0 12px;color:#444">Please complete the security check to continue.</p>'
        f'<div id="captcha-widget">{_iframe(provider, mode, origin, widget_id)}</div>'
        f'<textarea name="{field}" style="display:none"></textarea></div></div>'
    )


def captcha_overlay_template(provider: str, origin: Origin, widget_id: str = "0a1b2c") -> str:
    """The overlay as an inert ``<template>``: a page script inserts it when a challenge is "triggered"."""
    return f'<template id="captcha-overlay-template">{captcha_overlay(provider, origin, widget_id)}</template>'


CAPTCHA_LISTENER_JS = """
window.__requireCaptcha = function (fieldName, proceed) {
  var field = document.querySelector('form [name="' + fieldName + '"]');
  if (field && field.value) { proceed(); return; }
  var tpl = document.getElementById('captcha-overlay-template');
  if (!tpl || document.getElementById('captcha-overlay')) { return; }
  document.body.appendChild(tpl.content.cloneNode(true));
  var once = function () { document.removeEventListener('mock-captcha-solved', once); proceed(); };
  document.addEventListener('mock-captcha-solved', once);
};
window.addEventListener('message', function (event) {
  var data = event.data;
  if (!data || data.type !== 'mock-captcha') { return; }
  var names = ['h-captcha-response', 'g-recaptcha-response', 'cf-turnstile-response', 'fc-token'];
  names.forEach(function (n) {
    document.querySelectorAll('[name="' + n + '"]').forEach(function (el) { el.value = data.token; });
  });
  window.__captchaToken = data.token;
  var overlay = document.getElementById('captcha-overlay');
  if (overlay) { overlay.remove(); }
  document.dispatchEvent(new CustomEvent('mock-captcha-solved', {detail: data}));
});
"""


def recaptcha_badge(origin: Origin) -> str:
    """The invisible reCAPTCHA badge exactly as Google injects it (present on many real ATS pages)."""
    src = frame_url("recaptcha", origin, "invisible", "badge01")
    return (
        '<div class="grecaptcha-badge" data-style="bottomright" style="width:256px;height:60px;'
        "display:block;transition:right .3s ease 0s;position:fixed;bottom:14px;right:-186px;"
        'box-shadow:gray 0 0 5px;border-radius:2px;overflow:hidden;">'
        '<div class="grecaptcha-logo"><iframe title="reCAPTCHA" width="256" height="60" '
        'role="presentation" name="a-badge01" frameborder="0" scrolling="no" '
        'sandbox="allow-forms allow-popups allow-same-origin allow-scripts allow-top-navigation '
        f'allow-modals allow-popups-to-escape-sandbox" src="{esc(src)}"></iframe></div>'
        '<div class="grecaptcha-error"></div>'
        '<textarea id="g-recaptcha-response-100000" name="g-recaptcha-response" '
        'class="g-recaptcha-response" style="width:250px;height:40px;border:1px solid #c1c1c1;'
        'margin:10px 25px;padding:0;resize:none;display:none;"></textarea></div>'
        '<iframe style="display:none"></iframe>'
    )


_FRAME_TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>__TITLE__</title>
<style>
html,body{margin:0;padding:0;font-family:Roboto,Helvetica,Arial,sans-serif;background:#fafafa;color:#222}
.box{box-sizing:border-box;width:100%;height:100%;min-height:65px;border:1px solid #d3d3d3;border-radius:3px;
 background:#f9f9f9;display:flex;align-items:center;justify-content:space-between;padding:0 12px}
.left{display:flex;align-items:center}
.cb{width:26px;height:26px;border:2px solid #c1c1c1;border-radius:3px;background:#fff;cursor:pointer;
 margin-right:12px;display:flex;align-items:center;justify-content:center;font-size:18px}
.cb[aria-checked=true]{border-color:#2e7d32;color:#2e7d32}
.brand{font-size:10px;color:#555;text-align:center}
.challenge{padding:14px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;margin:12px 0}
.tile{height:96px;background:#b0bec5;cursor:pointer;border:3px solid transparent}
.tile.on{border-color:#1a73e8}
button{background:#1a73e8;color:#fff;border:0;border-radius:3px;padding:10px 22px;font-size:14px;cursor:pointer}
.msg{font-size:12px;color:#b71c1c;min-height:16px}
</style></head><body data-provider="__PROVIDER__" data-mode="__MODE__">
<div id="root"></div>
<script>
(function () {
  var provider = "__PROVIDER__", mode = "__MODE__", stuck = __STUCK__;
  if (provider === 'hcaptcha') { mode = /frame=challenge/.test(location.hash) ? 'challenge' : 'checkbox'; }
  var labels = {hcaptcha: 'I am human', recaptcha: "I'm not a robot", turnstile: 'Verify you are human',
                arkose: 'Verify'};
  var brand = {hcaptcha: 'hCaptcha', recaptcha: 'reCAPTCHA', turnstile: 'Cloudflare', arkose: 'Arkose Labs'};
  var root = document.getElementById('root');
  var solved = false;
  function solve(done) {
    if (solved) { return; }
    solved = true;
    fetch('/captcha/solve', {method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({provider: provider, mode: mode, stuck: stuck})})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, body: j}; }); })
      .then(function (res) {
        if (!res.ok) { solved = false; done(false, null); return; }
        done(true, res.body.token);
        parent.postMessage({type: 'mock-captcha', provider: provider, token: res.body.token}, '*');
      });
  }
  if (mode === 'invisible') { root.innerHTML = '<div style="width:70px;height:60px"></div>'; return; }
  if (mode === 'challenge') {
    var tiles = '';
    for (var i = 0; i < 9; i++) { tiles += '<div class="tile" role="button" aria-label="Tile ' + (i + 1) + '"></div>'; }
    root.innerHTML = '<div class="challenge"><strong>Select all images with a bus</strong>' +
      '<div class="grid">' + tiles + '</div><div class="msg" id="msg"></div>' +
      '<button type="button" id="verify-button" class="button-submit">Verify</button>' +
      '<div class="brand" style="margin-top:8px">' + brand[provider] + '</div></div>';
    root.querySelectorAll('.tile').forEach(function (t) {
      t.addEventListener('click', function () { t.classList.toggle('on'); });
    });
    document.getElementById('verify-button').addEventListener('click', function () {
      solve(function (ok) { document.getElementById('msg').textContent = ok ? 'Verified' : 'Try again'; });
    });
    return;
  }
  root.innerHTML = '<div class="box"><div class="left"><div class="cb" id="checkbox" role="checkbox" ' +
    'aria-checked="false" tabindex="0"></div><span id="label">' + labels[provider] + '</span></div>' +
    '<div class="brand">' + brand[provider] + '<br>Privacy - Terms</div></div>';
  var cb = document.getElementById('checkbox'), label = document.getElementById('label');
  function activate() {
    label.textContent = provider === 'turnstile' ? 'Verifying...' : label.textContent;
    solve(function (ok) {
      cb.setAttribute('aria-checked', ok ? 'true' : 'false');
      cb.textContent = ok ? '\\u2713' : '';
      label.textContent = ok ? (provider === 'turnstile' ? 'Success!' : labels[provider])
                             : 'Verification failed. Try again.';
    });
  }
  cb.addEventListener('click', activate);
  cb.addEventListener('keydown', function (e) { if (e.key === ' ' || e.key === 'Enter') { activate(); } });
})();
</script></body></html>"""

_FRAME_TITLES = {
    "hcaptcha": "hCaptcha",
    "recaptcha": "reCAPTCHA",
    "turnstile": "Widget containing a Cloudflare security challenge",
    "arkose": "Arkose Labs",
}


def _frame_response(provider: str, mode: str, stuck: bool = False) -> Response:
    page = (
        _FRAME_TEMPLATE.replace("__TITLE__", _FRAME_TITLES[provider])
        .replace("__PROVIDER__", provider)
        .replace("__MODE__", mode)
        .replace("__STUCK__", "true" if stuck else "false")
    )
    return HTMLResponse(page)


def install_captcha_routes(site: MockSite, *, stuck: bool = False) -> None:
    """Serve the vendor frame pages and the ``/captcha/solve`` endpoint (idempotent per site).

    ``stuck=True`` makes every solve attempt fail (used by the Cloudflare wall: bots never get through).
    """
    if site.state.get("_captcha_routes"):
        return
    site.state["_captcha_routes"] = True
    lock = threading.Lock()
    captcha_interactions(site)
    site.state.setdefault("captcha_tokens", set())
    app = site.app

    @app.get("/captcha/v1/{version}/static/hcaptcha.html")
    def hcaptcha_frame(version: str) -> Response:
        return _frame_response("hcaptcha", "checkbox")

    @app.get("/recaptcha/{kind}/anchor")
    def recaptcha_anchor(kind: str, request: Request) -> Response:
        invisible = request.query_params.get("size") == "invisible"
        return _frame_response("recaptcha", "invisible" if invisible else "checkbox")

    @app.get("/recaptcha/{kind}/bframe")
    def recaptcha_bframe(kind: str) -> Response:
        return _frame_response("recaptcha", "challenge")

    @app.get("/cdn-cgi/challenge-platform/{rest:path}")
    def turnstile_frame(rest: str) -> Response:
        return _frame_response("turnstile", "checkbox", stuck=stuck)

    @app.get("/fc/assets/{rest:path}")
    def arkose_frame(rest: str) -> Response:
        return _frame_response("arkose", "challenge")

    @app.post("/captcha/solve")
    async def solve(request: Request) -> Response:
        try:
            payload = await request.json()
        except ValueError:
            payload = {}
        record = {
            "provider": str(payload.get("provider", "")),
            "mode": str(payload.get("mode", "")),
            "at": time.time(),
            "accepted": not (stuck or payload.get("stuck")),
        }
        with lock:
            captcha_interactions(site).append(record)
            if not record["accepted"]:
                return JSONResponse({"error": "verification failed"}, status_code=403)
            token = f"mock-captcha-{record['provider']}-{len(site.state['captcha_tokens']) + 1}"
            site.state["captcha_tokens"].add(token)
        return JSONResponse({"token": token})


# --------------------------------------------------------------------------------- factory helpers

_PAGE_CSS = """
<style>
body{margin:0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;color:#1f2933;background:#f5f7fa}
header.site{background:#102a43;color:#fff;padding:14px 24px;font-size:18px;font-weight:600}
main{max-width:760px;margin:32px auto;background:#fff;padding:28px 32px;border-radius:6px;
 box-shadow:0 1px 4px rgba(0,0,0,.12)}
h1{margin:0 0 6px;font-size:26px}
.meta{color:#52606d;margin-bottom:18px}
.notice{border-left:4px solid #b71c1c;background:#fdecea;padding:12px 16px;margin:18px 0;font-size:16px}
.btn{display:inline-block;background:#0b6bcb;color:#fff;padding:11px 22px;border-radius:4px;
 text-decoration:none;font-weight:600;border:0;font-size:15px;cursor:pointer}
input{padding:8px;font-size:15px;margin:4px 0;display:block}
</style>
"""


def _shell(company_name: str, inner: str, delay_s: float = 0.0) -> str:
    content = deferred_html(inner, delay_s)
    return f"<header class='site'>{esc(company_name)} Careers</header><main>{content}</main>"


def _display_name(company: str) -> str:
    return company.replace("-", " ").replace("_", " ").title()


def _default_job(title: str = "Business Operations Intern, Summer 2027") -> MockJob:
    return MockJob(id="7001", title=title)


def _job_for(site: MockSite, job_id: str) -> MockJob | None:
    return site.jobs.get(job_id)


# --------------------------------------------------------------------------------- cloudflare_wall


def cloudflare_wall(
    company: str = "acme",
    jobs: Sequence[MockJob] | None = None,
    *,
    host: str | None = None,
    name: str = "cloudflare_wall",
    status: int = 403,
) -> MockSite:
    """Every request gets the Cloudflare "Just a moment..." managed challenge (never passable by a bot)."""
    site = MockSite(name, host or f"careers.{company}.com")
    for job in job_or_default(jobs, _default_job()):
        site.jobs[job.id] = job
    install_captcha_routes(site, stuck=True)
    ray = "8a1b2c3d4e5f6a7b-DFW"

    def wall(request: Request) -> Response:
        origin = Origin.of(request)
        widget = _iframe("turnstile", "checkbox", origin, "wall01")
        body = (
            '<div class="main-wrapper" role="main"><div class="main-content">'
            "<noscript><div class='h2'><span id='challenge-error-text'>"
            "Enable JavaScript and cookies to continue</span></div></noscript>"
            f'<h1 class="zone-name-title h1">{esc(site.host)}</h1>'
            '<p id="challenge-running" class="h2 spacer-bottom">Verify you are human by completing '
            "the action below.</p>"
            f'<div id="challenge-stage" class="spacer">{widget}</div>'
            f'<div id="challenge-body-text" class="core-msg spacer">{esc(site.host)} needs to review '
            "the security of your connection before proceeding.</div></div></div>"
            '<div class="footer" role="contentinfo"><div class="footer-inner">'
            f'<div class="ray-id">Ray ID: <code>{ray}</code></div>'
            '<div class="text-center" id="footer-text">Performance &amp; security by '
            "<a rel='noopener noreferrer' href='#'>Cloudflare</a></div></div></div>"
        )
        head = (
            "<meta name='robots' content='noindex,nofollow'>"
            "<style>body{font-family:system-ui,Arial,sans-serif;margin:0;text-align:left;color:#313131}"
            ".main-content{margin:8rem auto;max-width:60rem;padding:0 1.5rem}.h1{font-size:2.5rem}"
            ".h2{font-size:1.5rem;font-weight:500}.spacer{margin:2rem 0}.footer{text-align:center;"
            "font-size:.75rem;margin-top:4rem}</style>"
        )
        return html_response(
            "Just a moment...",
            body,
            head,
            status=status,
            body_attrs="class='no-js'",
            headers={
                "cf-mitigated": "challenge",
                "server": "cloudflare",
                "cf-ray": ray,
                "cache-control": "private, max-age=0, no-store, no-cache, must-revalidate",
            },
        )

    site.app.add_api_route("/", wall, methods=["GET", "POST"], response_model=None)
    site.app.add_api_route(
        "/{full_path:path}",
        wall,
        methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"],
        response_model=None,
    )
    return site


# --------------------------------------------------------------------------------- sso_only


def sso_only(
    company: str = "acme",
    jobs: Sequence[MockJob] | None = None,
    *,
    host: str | None = None,
    name: str = "sso_only",
    idp_name: str = "Okta",
    render_delay_s: float = 0.0,
) -> MockSite:
    """A careers portal that only lets employees/members sign in through the company SSO.

    ``render_delay_s`` > 0 renders the sign-in content by script after the delay (an early check sees an empty page).
    """
    site = MockSite(name, host or f"careers.{company}.com")
    company_name = _display_name(company)
    for job in job_or_default(jobs, _default_job()):
        site.jobs[job.id] = job
    site.state["idp_visits"] = []

    def login_page() -> Response:
        inner = (
            "<h1>Sign in to continue</h1>"
            f"<p class='meta'>{esc(company_name)} careers</p>"
            "<p>Use your organization account to view and apply for open positions.</p>"
            "<a id='sso-login' class='btn sso-button' href='/sso/start'>"
            "Sign in with your company SSO</a>"
            "<p class='meta' style='margin-top:18px'>Applications are accepted only from "
            "authenticated accounts. There is no guest application.</p>"
        )
        return html_response(
            f"Sign in - {company_name} Careers",
            _shell(company_name, inner, render_delay_s),
            _PAGE_CSS,
        )

    @site.app.get("/sso/start")
    def sso_start() -> Response:
        return RedirectResponse("/sso/idp", status_code=302)

    @site.app.get("/sso/idp")
    def sso_idp() -> Response:
        site.state["idp_visits"].append(time.time())
        inner = (
            f"<h1>{esc(idp_name)}</h1><p>Sign in to {esc(company_name)}</p>"
            "<form method='post' action='/sso/idp'>"
            "<label>Username <input id='idp-username' name='username' type='text'></label>"
            "<label>Password <input id='idp-password' name='password' type='password'></label>"
            "<button class='btn' type='submit'>Sign in</button></form>"
        )
        return html_response(f"{idp_name} - Sign In", _shell(idp_name, inner), _PAGE_CSS)

    @site.app.post("/sso/idp")
    async def sso_idp_post() -> Response:
        return html_response(
            f"{idp_name} - Sign In",
            _shell(idp_name, "<div class='notice'>Authentication failed.</div>"),
            _PAGE_CSS,
            status=401,
        )

    site.app.add_api_route("/", login_page, methods=["GET"], response_model=None)
    site.app.add_api_route("/{full_path:path}", login_page, methods=["GET"], response_model=None)
    return site


# --------------------------------------------------------------------------------- closed_posting

ClosedVariant = Literal["closed", "filled", "expired", "not_found"]

_CLOSED_TEXT: dict[str, str] = {
    "closed": "This job is no longer accepting applications.",
    "filled": "Sorry, this position has been filled.",
    "expired": "This posting has expired and can no longer be applied to.",
}


def closed_posting(
    company: str = "acme",
    jobs: Sequence[MockJob] | None = None,
    *,
    variant: ClosedVariant = "closed",
    host: str | None = None,
    name: str = "closed_posting",
    render_delay_s: float = 0.0,
) -> MockSite:
    """A posting that cannot be applied to: HTTP 200 with closed text, or HTTP 404 (``not_found``).

    ``render_delay_s`` > 0 renders the page content by script after the delay (title and status stay immediate).
    """
    site = MockSite(name, host or f"careers.{company}.com")
    company_name = _display_name(company)
    for job in job_or_default(jobs, _default_job()):
        site.jobs[job.id] = job

    def page(request: Request) -> Response:
        job = next(iter(site.jobs.values()))
        parts = [p for p in request.url.path.split("/") if p]
        for part in reversed(parts):
            if part in site.jobs:
                job = site.jobs[part]
                break
        if variant == "not_found":
            inner = (
                "<h1>Sorry, we couldn't find anything here</h1>"
                "<p>The page you are looking for does not exist.</p>"
                "<a class='btn' href='/'>See all open positions</a>"
            )
            return html_response(
                f"Not found - {company_name} Careers",
                _shell(company_name, inner),
                _PAGE_CSS,
                status=404,
            )
        inner = (
            f"<h1>{esc(job.title)}</h1><div class='meta'>{esc(job.location)}</div>"
            f"<div class='notice' role='alert' id='posting-closed'>{esc(_CLOSED_TEXT[variant])}</div>"
            "<a class='btn' href='/'>Search for other jobs</a>"
        )
        return html_response(
            f"{job.title} - {company_name} Careers",
            _shell(company_name, inner, render_delay_s),
            _PAGE_CSS,
        )

    site.app.add_api_route("/", page, methods=["GET"], response_model=None)
    site.app.add_api_route("/{full_path:path}", page, methods=["GET"], response_model=None)
    return site


# --------------------------------------------------------------------------------- already_applied


def already_applied(
    company: str = "acme",
    jobs: Sequence[MockJob] | None = None,
    *,
    host: str | None = None,
    name: str = "already_applied",
    message: str = "You have already applied to this job.",
    render_delay_s: float = 0.0,
) -> MockSite:
    """A posting page that tells the (signed-in) candidate they already applied; there is no form.

    ``render_delay_s`` > 0 renders the page content by script after the delay.
    """
    site = MockSite(name, host or f"careers.{company}.com")
    company_name = _display_name(company)
    for job in job_or_default(jobs, _default_job()):
        site.jobs[job.id] = job

    def page(request: Request) -> Response:
        job = next(iter(site.jobs.values()))
        for part in reversed([p for p in request.url.path.split("/") if p]):
            if part in site.jobs:
                job = site.jobs[part]
                break
        inner = (
            f"<h1>{esc(job.title)}</h1><div class='meta'>{esc(job.location)}</div>"
            f"<div class='notice' role='status' id='already-applied' style='border-color:#2e7d32;"
            f"background:#e8f5e9'>{esc(message)}</div>"
            "<p>Application status: <strong>Under review</strong></p>"
            "<a class='btn' href='/applications'>View your applications</a>"
        )
        return html_response(
            f"{job.title} - {company_name} Careers",
            _shell(company_name, inner, render_delay_s),
            _PAGE_CSS,
        )

    site.app.add_api_route("/", page, methods=["GET"], response_model=None)
    site.app.add_api_route("/{full_path:path}", page, methods=["GET"], response_model=None)
    return site


# --------------------------------------------------------------------------------- employer_redirect

TargetPath = str | Callable[[MockSite], str]


def employer_redirect(
    company: str = "acme",
    jobs: Sequence[MockJob] | None = None,
    *,
    target: str = "workday",
    target_path: TargetPath = "/",
    hops: int = 1,
    status: int = 302,
    new_tab: bool = False,
    host: str | None = None,
    name: str = "employer_redirect",
    render_delay_s: float = 0.0,
) -> MockSite:
    """An employer careers page whose "Apply now" link redirects into another hub site.

    ``target`` is the NAME of a site registered in the same ``MockHub``; the destination URL is resolved when
    the redirect is served (so the hub may start after this site is created). ``target_path`` may be a string
    or a callable receiving the target site. ``hops`` extra same-site 302 hops are inserted before the final
    redirect to mimic tracking redirectors. ``render_delay_s`` > 0 renders the job page content (including the
    "Apply now" link) by script after the delay.
    """
    site = MockSite(name, host or f"careers.{company}.com")
    company_name = _display_name(company)
    for job in job_or_default(jobs, _default_job()):
        site.jobs[job.id] = job
    site.state["redirects_served"] = 0

    @site.app.get("/")
    def index() -> Response:
        links = "".join(
            f"<li><a href='/jobs/{esc(j.id)}'>{esc(j.title)}</a> - {esc(j.location)}</li>"
            for j in site.jobs.values()
        )
        return html_response(
            f"{company_name} Careers",
            _shell(company_name, f"<h1>Open positions</h1><ul>{links}</ul>"),
            _PAGE_CSS,
        )

    @site.app.get("/jobs/{job_id}")
    def job_page(job_id: str) -> Response:
        job = _job_for(site, job_id)
        if job is None:
            return html_response(
                "Not found", _shell(company_name, "<h1>Not found</h1>"), _PAGE_CSS, status=404
            )
        blank = ' target="_blank" rel="noopener"' if new_tab else ""
        inner = (
            f"<h1>{esc(job.title)}</h1><div class='meta'>{esc(job.location)}</div>"
            f"<p>{esc(job.description)}</p>"
            f"<a id='apply-now' class='btn apply-btn' href='/jobs/{esc(job_id)}/apply'{blank}>Apply now</a>"
        )
        return html_response(
            f"{job.title} - {company_name} Careers",
            _shell(company_name, inner, render_delay_s),
            _PAGE_CSS,
        )

    def resolve_target() -> str | None:
        hub = site.hub
        if hub is None or target not in hub.sites:
            return None
        other = hub.site(target)
        if other.port is None:
            return None
        path = target_path(other) if callable(target_path) else target_path
        return other.url(path)

    @site.app.get("/jobs/{job_id}/apply")
    def apply_link(job_id: str) -> Response:
        if hops > 0:
            return RedirectResponse(f"/jobs/{job_id}/apply/hop/1", status_code=status)
        return _final_redirect()

    @site.app.get("/jobs/{job_id}/apply/hop/{n}")
    def apply_hop(job_id: str, n: int) -> Response:
        if n < hops:
            return RedirectResponse(f"/jobs/{job_id}/apply/hop/{n + 1}", status_code=status)
        return _final_redirect()

    def _final_redirect() -> Response:
        destination = resolve_target()
        if destination is None:
            return html_response(
                "Bad gateway",
                f"<h1>502</h1><p>mock hub has no started site named {esc(target)}</p>",
                status=502,
            )
        site.state["redirects_served"] += 1
        return RedirectResponse(destination, status_code=status)

    return site


# --------------------------------------------------------------------------------- greenhouse-based


def captcha_visible(
    company: str = "acme",
    jobs: Sequence[MockJob] | None = None,
    *,
    provider: Provider | None = None,
    placement: Placement = "overlay",
    base: Literal["greenhouse", "lever"] = "greenhouse",
    name: str = "captcha_visible",
    **options: Any,
) -> MockSite:
    """An application form gated by a VISIBLE challenge (default: modal overlay over a Greenhouse form)."""
    if base == "lever":
        from autoapply.testing.mock_ats import lever

        return lever.make_site(
            company,
            jobs,
            name=name,
            require_captcha=True,
            captcha_provider=provider or "hcaptcha",
            captcha_placement=placement,
            **options,
        )
    from autoapply.testing.mock_ats import greenhouse

    return greenhouse.make_site(
        company,
        jobs,
        name=name,
        require_captcha=True,
        captcha_provider=provider or "recaptcha",
        captcha_placement=placement,
        **options,
    )


def invisible_badge(
    company: str = "acme",
    jobs: Sequence[MockJob] | None = None,
    *,
    name: str = "invisible_badge",
    **options: Any,
) -> MockSite:
    """A normal, submittable Greenhouse form that only carries the invisible reCAPTCHA badge."""
    from autoapply.testing.mock_ats import greenhouse

    return greenhouse.make_site(company, jobs, name=name, invisible_recaptcha=True, **options)


_FACTORIES: dict[str, Callable[..., MockSite]] = {
    "captcha_visible": captcha_visible,
    "invisible_badge": invisible_badge,
    "cloudflare_wall": cloudflare_wall,
    "sso_only": sso_only,
    "closed_posting": closed_posting,
    "already_applied": already_applied,
    "employer_redirect": employer_redirect,
}

BLOCKER_KINDS: tuple[str, ...] = tuple(_FACTORIES)


def make_site(
    kind: str = "captcha_visible",
    company: str = "acme",
    jobs: Sequence[MockJob] | None = None,
    **options: Any,
) -> MockSite:
    """Build the blocker ``kind`` (one of ``BLOCKER_KINDS``)."""
    try:
        factory = _FACTORIES[kind]
    except KeyError:
        raise ValueError(
            f"unknown blocker {kind!r}; expected one of {sorted(_FACTORIES)}"
        ) from None
    return factory(company, jobs, **options)
