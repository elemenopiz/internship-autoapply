"""Enumerate an application form's questions in one DOM pass.

``scan_form`` runs a single ``page.evaluate`` that walks the application form
(the largest form holding a file or email input, else the whole document,
including open shadow roots) and returns plain descriptors;
``questions_from_scan`` turns them into typed ``Question`` values. Keeping the
Python side pure lets tests feed descriptors directly.

Question kinds:
  text, textarea, select   native controls (contenteditable boxes are textarea)
  combobox                 ARIA/React select or a "Select an option" button —
                           options appear only when opened
  radio, checkboxes        same-name groups (or ARIA radiogroups), one Question
                           per group
  checkbox                 a lone checkbox (usually a consent/opt-in)
  yesno                    a pair of Yes/No buttons (Ashby)
  file                     upload input (resume / cover letter)
  password                 never filled

Labels come from, in order: the control's own label (aria-labelledby,
label[for], a wrapping label, aria-label, a custom-element host's label), the
nearest text right before the field (a heading, span or div — many ATSs
never use <label>), the older ancestor label search, the placeholder, and
finally the humanized input name. Honeypot fields (off-screen or inside an
aria-hidden wrapper) are skipped.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

_REQUIRED_MARK = re.compile(r"\s*[*✱]+\s*$")
#: "Email* (required)", "First name* Required", "(Required) Allow us to...",
#: "Required. By submitting..." — explicit required markers besides a star.
_REQUIRED_WORD_END = re.compile(r"\s*(?:[*✱]\s*\(?required\)?|\(required\))\s*\.?\s*$", re.IGNORECASE)
_REQUIRED_WORD_START = re.compile(r"^\s*(?:\(required\)|required[.:])\s*", re.IGNORECASE)
_ZERO_WIDTH = re.compile(r"[​-‍⁠﻿]")

_SCAN_JS = r"""
() => {
  window.__aaqCounter = window.__aaqCounter || 0;
  const clean = (t) => (t || '').replace(/[​-‍⁠﻿]/g, '').replace(/\s+/g, ' ').trim();
  const CTRL = 'input:not([type="hidden"]), select, textarea, [role="combobox"], [role="radiogroup"], '
    + '[role="radio"], [role="checkbox"], [contenteditable="true"]';

  // ---------- tree helpers (open shadow roots included)
  const rootOf = (el) => (el.getRootNode ? el.getRootNode() : document);
  const parentOf = (el) => el.parentElement
    || (rootOf(el) instanceof ShadowRoot ? rootOf(el).host : null);
  const ordered = (root, sel) => {
    const out = [];
    const rec = (node) => {
      for (let c = node.firstElementChild; c; c = c.nextElementSibling) {
        if (c.matches(sel)) out.push(c);
        if (c.shadowRoot) rec(c.shadowRoot);
        rec(c);
      }
    };
    rec(root);
    return out;
  };
  const contains = (a, b) => { for (let n = b; n; n = parentOf(n)) if (n === a) return true; return false; };
  const hasControl = (n) => {
    if (n.matches && n.matches(CTRL)) return true;
    if (n.querySelector && n.querySelector(CTRL)) return true;
    if (n.querySelectorAll) {
      for (const e of n.querySelectorAll('*')) if (e.shadowRoot && e.shadowRoot.querySelector(CTRL)) return true;
    }
    return false;
  };

  // ---------- visibility
  const visible = (el) => {
    if (!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none';
  };
  const inAriaHidden = (el) => {
    for (let n = el; n && n !== document.body; n = parentOf(n)) {
      if (n.getAttribute && n.getAttribute('aria-hidden') === 'true') return true;
    }
    return false;
  };
  const offscreen = (el) => {
    const r = el.getBoundingClientRect();
    return r.right + window.scrollX <= 0 || r.bottom + window.scrollY <= 0;
  };
  const HONEYPOT = /(^|[^a-z])(hp|honeypot|bot_?field|leave_?(this_?)?blank|do_?not_?fill)([^a-z]|$)/i;
  const honeypot = (el) => HONEYPOT.test(el.name || '') || HONEYPOT.test(el.id || '')
    || (inAriaHidden(el) && offscreen(el)) || (offscreen(el) && el.tabIndex < 0);
  const labelEl = (el) => (el.id && rootOf(el).querySelector(`label[for="${CSS.escape(el.id)}"]`))
    || el.closest('label');
  // Styled radios/checkboxes hide the real input and show its label or an
  // ARIA wrapper (role="radio") instead.
  const optionFace = (el) => labelEl(el) || el.closest('[role="radio"], [role="checkbox"]');
  const shown = (el) => visible(el) || (['radio', 'checkbox'].includes(el.type)
      && !!optionFace(el) && visible(optionFace(el)));

  // ---------- selectors
  const selectorOf = (el) => {
    if (el.id && rootOf(el) === document && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) {
      return '#' + CSS.escape(el.id);
    }
    if (!el.dataset.aaq) el.dataset.aaq = String(++window.__aaqCounter);
    return `[data-aaq="${el.dataset.aaq}"]`;
  };

  // ---------- text
  const SKIP = new Set(['SELECT', 'OPTION', 'TEXTAREA', 'INPUT', 'SCRIPT', 'STYLE', 'NOSCRIPT',
    'TEMPLATE', 'SVG', 'svg', 'BUTTON']);
  const SKIP_ROLES = new Set(['listbox', 'option', 'combobox', 'menu', 'tooltip']);
  const textOf = (node, max = 700) => {
    let out = '';
    const rec = (n, top) => {
      if (out.length > max) return;
      if (n.nodeType === 3) { out += ' ' + n.nodeValue; return; }
      if (n.nodeType !== 1) return;
      if (!top) {
        if (SKIP.has(n.tagName)) return;
        if (SKIP_ROLES.has((n.getAttribute('role') || '').toLowerCase())) return;
        const st = getComputedStyle(n);
        if (st.display === 'none' || st.visibility === 'hidden') return;
      }
      for (let c = n.firstChild; c; c = c.nextSibling) rec(c, false);
    };
    rec(node, true);
    return clean(out).slice(0, max);
  };
  const byIds = (el, ids) => clean(ids.split(/\s+/).map((id) => {
    const r = rootOf(el);
    const n = (r.getElementById && r.getElementById(id)) || document.getElementById(id);
    return n ? textOf(n) : '';
  }).join(' '));
  // Labels that name the widget, not the question.
  const WEAK = /^(select|select\.\.\.|select an option|select one|select\s*\.\.\.|choose|choose one|choose an option|please select|please choose|search|search\.\.\.|type to search|start typing.*|type here|file|files|upload|upload file|upload a file|attach|attach file|attach a file|browse|choose file|choose files|select file|select a file|drop or select.*|drag.*drop.*|click to upload.*|total \d+ files? selected|no file chosen|your answer|answer|enter text|--|\+?\d{1,3}( ?[a-z]{2})?)$/i;
  const weak = (t) => !t || WEAK.test(t);

  const hostLabel = (el) => {
    const r = rootOf(el);
    if (!(r instanceof ShadowRoot)) return '';
    const h = r.host;
    const direct = clean(h.getAttribute('label') || h.getAttribute('aria-label') || h.getAttribute('title'));
    if (direct) return direct;
    const hl = h.getAttribute('aria-labelledby');
    if (hl) { const t = byIds(h, hl); if (t) return t; }
    return ownLabel(h);
  };
  const ownLabel = (el) => {
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = byIds(el, lb); if (!weak(t)) return t; }
    if (el.id) {
      const l = rootOf(el).querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) { const t = textOf(l); if (!weak(t)) return t; }
    }
    const wrap = el.closest('label');
    if (wrap) {
      const wl = wrap.getAttribute('aria-labelledby');
      const t = wl ? byIds(wrap, wl) : textOf(wrap);
      if (!weak(t)) return t;
    }
    const aria = clean(el.getAttribute('aria-label'));
    if (!weak(aria)) return aria;
    const host = hostLabel(el);
    if (!weak(host)) return host;
    return '';
  };
  const LABELISH = /^(LABEL|LEGEND|H1|H2|H3|H4|H5|H6|STRONG|B|DT)$/;
  const labelish = (n) => LABELISH.test(n.tagName)
    || /label|title|question|heading/i.test(`${n.id || ''} ${typeof n.className === 'string' ? n.className : ''}`);
  // The text just before the field: many ATSs put the question in a div,
  // span or heading that precedes the input. Siblings are collected up to the
  // first label-like one; another field's territory stops the search.
  const precedingText = (el, maxDepth = 7) => {
    const ph = clean(el.getAttribute && el.getAttribute('placeholder'));
    const tel = el.tagName === 'INPUT' && (el.type === 'tel' || /phone/i.test(el.name || el.id || ''));
    let node = el;
    for (let depth = 0; depth < maxDepth && node && node !== document.body; depth++) {
      const texts = [];
      let blocked = false;
      for (let sib = node.previousElementSibling; sib && texts.length < 3; sib = sib.previousElementSibling) {
        if (hasControl(sib)) { blocked = true; break; }
        const t = textOf(sib);
        if (t && !weak(t)) {
          texts.unshift(t);
          if (labelish(sib)) break;
        }
      }
      if (texts.length) return clean(texts.join(' '));
      // another field sits right before this one (a phone input's country
      // picker is the exception: the label precedes both)
      if (blocked && !tel) return '';
      const up = parentOf(node);
      if (!up || up === document.body) break;
      // climbing out of a container that holds other fields reaches a
      // section heading; this field's own placeholder names it better
      if (!weak(ph) && Array.from(up.children).some((c) => c !== node && hasControl(c))) return '';
      node = up;
    }
    return '';
  };
  // Older heuristic: the first label-like element in an ancestor.
  const containerLabel = (start) => {
    let node = start;
    for (let depth = 0; depth < 6 && node && node !== document.body; depth++) {
      if (node.tagName === 'FIELDSET') {
        const lg = node.querySelector('legend');
        if (lg && !weak(textOf(lg))) return textOf(lg);
      }
      const cands = node.querySelectorAll(
        'legend, label, .application-label, [class*="label"], [class*="Label"], [id$="-label"]');
      for (const c of cands) {
        if (hasControl(c)) continue;
        const t = textOf(c);
        if (!weak(t)) return t;
      }
      node = parentOf(node);
    }
    return '';
  };
  const humanName = (el) => {
    const raw = el.getAttribute('name') || el.id || '';
    if (/^[0-9a-f-]{16,}$/i.test(raw) || /^:r/.test(raw)) return '';
    return clean(raw.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[\[\]_.\-]+/g, ' ')
      .replace(/\b(input|value|field|question|answer|resumator|candidate|application|form|attributes|\d+)\b/gi, ' '));
  };
  const labelOf = (el) => ownLabel(el) || precedingText(el) || containerLabel(parentOf(el))
    || clean(el.getAttribute('placeholder')) || humanName(el);
  const isRequired = (el) => !!(el.required || el.getAttribute('aria-required') === 'true');
  // ids/classes of the field's ancestors, e.g. "education--0 ..." — tells an
  // education "End date year" apart from an employment one.
  const contextOf = (el) => {
    const parts = [];
    let node = parentOf(el);
    for (let i = 0; i < 8 && node && node !== document.body; i++, node = parentOf(node)) {
      parts.push(node.id || '', typeof node.className === 'string' ? node.className : '');
    }
    return parts.join(' ').toLowerCase().slice(0, 400);
  };
  const chrome = (el) => !!el.closest('header, nav, footer, [role="banner"], [role="navigation"], [role="contentinfo"]');
  // A country-code picker sitting next to a phone input.
  const phoneBuddy = (el) => {
    let node = parentOf(el);
    for (let i = 0; i < 4 && node && node !== document.body; i++, node = parentOf(node)) {
      const fields = node.querySelectorAll('input:not([type="hidden"]), select, textarea, [role="combobox"]');
      if (fields.length > 3) return false;  // a bigger container: not a phone widget
      const tel = node.querySelector('input[type="tel"], input[name*="phone" i], input[id*="phone" i], input[autocomplete="tel"]');
      if (tel && tel !== el) return true;
    }
    return false;
  };

  // ---------- scope: the biggest form with a file/email input
  const forms = ordered(document, 'form').filter((f) => f.querySelector('input[type="file"], input[type="email"]'));
  forms.sort((a, b) => b.querySelectorAll('input, select, textarea').length - a.querySelectorAll('input, select, textarea').length);
  const scope = forms[0] || document;
  const outOfScope = (el) => scope === document && chrome(el);

  // document position of every element (shadow roots included), so the
  // questions come back in reading order whatever pass found them
  const pos = new Map();
  let counter = 0;
  const index = (node) => {
    for (let c = node.firstElementChild; c; c = c.nextElementSibling) {
      pos.set(c, counter++);
      if (c.shadowRoot) index(c.shadowRoot);
      index(c);
    }
  };
  index(scope);
  const out = [];
  const add = (anchor, desc) => { desc.order = pos.has(anchor) ? pos.get(anchor) : 1e9; out.push(desc); };
  const groups = new Map();
  const base = (el) => ({ inputType: '', name: el.getAttribute('name') || el.id || '', maxLength: null,
    accept: '', placeholder: clean(el.getAttribute('placeholder')), current: '', context: contextOf(el) });

  ordered(scope, 'input, select, textarea, [contenteditable="true"]').forEach((el) => {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image', 'search'].includes(type)) return;
    if (outOfScope(el)) return;
    const isOption = type === 'radio' || type === 'checkbox';
    if (type !== 'file' && !shown(el)) return;
    // React-select renders an invisible required <input aria-hidden> next to
    // each dropdown purely for native validation — not a question. Styled
    // radios/checkboxes may be aria-hidden behind a visible wrapper.
    if (type !== 'file' && el.getAttribute('aria-hidden') === 'true' && !(isOption && optionFace(el))) return;
    if (type !== 'file' && !isOption && honeypot(el)) return;

    if (isOption && el.name) {
      if (!groups.has(el.name)) groups.set(el.name, []);
      groups.get(el.name).push(el);
      return;
    }
    let kind = 'text';
    if (tag === 'textarea' || el.isContentEditable) kind = 'textarea';
    else if (tag === 'select') kind = 'select';
    else if (role === 'combobox' || (el.getAttribute('aria-autocomplete') === 'list' && el.getAttribute('aria-haspopup'))) kind = 'combobox';
    else if (type === 'file') kind = 'file';
    else if (type === 'password') kind = 'password';
    else if (type === 'checkbox') kind = 'checkbox';
    else if (type === 'radio') kind = 'radio';

    const options = tag === 'select'
      ? Array.from(el.options).map((o) => clean(o.text))
          .filter((t) => t && !/^(select|choose|please select|--)/i.test(t))
      : [];
    let label = labelOf(el);
    if ((kind === 'select' || kind === 'combobox') && phoneBuddy(el) && !/country/i.test(label)) {
      label = 'Phone country code' + (label ? ` (${label})` : '');
    }
    const current = tag === 'select' ? clean(el.selectedIndex >= 0 ? el.options[el.selectedIndex].text : '')
      : isOption ? (el.checked ? 'checked' : '')
      : type === 'file' ? ''
      : (el.isContentEditable ? textOf(el) : clean(el.value)).slice(0, 200);
    add(el, {
      ...base(el), kind, label, required: isRequired(el), options, optionSelectors: [],
      selector: selectorOf(el), inputType: el.isContentEditable ? 'contenteditable' : type,
      maxLength: el.getAttribute('maxlength'), accept: el.getAttribute('accept') || '', current,
    });
  });

  // Checkboxes named "x-1", "x-2" (JazzHR) are one question's options.
  const merged = new Map();
  groups.forEach((members, name) => {
    const stem = (members[0].type === 'checkbox' && members.length === 1) ? name.replace(/[-_]\d+$/, '') : name;
    const key = stem !== name ? 'stem:' + stem : name;
    if (!merged.has(key)) merged.set(key, []);
    merged.get(key).push(...members);
  });

  const commonAncestor = (members) => {
    let node = parentOf(members[0]);
    while (node && !members.every((m) => contains(node, m))) node = parentOf(node);
    return node;
  };
  const groupLabel = (members, container) => {
    const first = members[0];
    const fs = first.closest('fieldset');
    if (fs && members.every((m) => contains(fs, m))) {
      const lg = fs.querySelector('legend');
      if (lg && !weak(textOf(lg))) return textOf(lg);
      const fl = fs.getAttribute('aria-labelledby');
      if (fl && !weak(byIds(fs, fl))) return byIds(fs, fl);
    }
    const rg = first.closest('[role="radiogroup"], [role="group"]');
    if (rg && members.every((m) => contains(rg, m))) {
      const rl = rg.getAttribute('aria-labelledby');
      if (rl && !weak(byIds(rg, rl))) return byIds(rg, rl);
      const ra = clean(rg.getAttribute('aria-label'));
      if (!weak(ra)) return ra;
    }
    if (container) {
      const t = precedingText(container);
      if (t) return t;
    }
    return containerLabel(container ? parentOf(container) : parentOf(first));
  };
  const optionLabel = (el) => {
    // the visible label beats a verbose aria-labelledby ("Question? yes")
    const l = labelEl(el);
    const t = l ? textOf(l) : '';
    if (t) return t;
    const face = el.closest('[role="radio"], [role="checkbox"]');
    if (face && textOf(face)) return textOf(face);
    return ownLabel(el) || clean(el.value);
  };

  merged.forEach((members, key) => {
    const type = (members[0].getAttribute('type') || '').toLowerCase();
    const name = members[0].name;
    if (type === 'checkbox' && members.length === 1) {
      const el = members[0];
      add(el, {
        ...base(el), kind: 'checkbox', label: labelOf(el), required: isRequired(el), options: [],
        optionSelectors: [], selector: selectorOf(el), inputType: type, name,
        current: el.checked ? 'checked' : '',
      });
      return;
    }
    const container = commonAncestor(members);
    const label = groupLabel(members, container) || name;
    const rg = members[0].closest('[role="radiogroup"], fieldset');
    add(members[0], {
      ...base(members[0]), kind: type === 'radio' ? 'radio' : 'checkboxes',
      label, required: members.some(isRequired) || (!!rg && rg.getAttribute('aria-required') === 'true'),
      options: members.map(optionLabel),
      optionSelectors: members.map(selectorOf),
      selector: selectorOf(members[0]), inputType: type, name: key.startsWith('stem:') ? key.slice(5) : name,
      current: members.filter((m) => m.checked).map(optionLabel).join(', '),
    });
  });

  // ARIA radio groups with no <input> at all
  ordered(scope, '[role="radiogroup"]').forEach((g) => {
    if (g.querySelector('input[type="radio"]') || !visible(g) || outOfScope(g)) return;
    const opts = Array.from(g.querySelectorAll('[role="radio"]')).filter(visible);
    if (!opts.length) return;
    add(g, {
      ...base(g), kind: 'radio', label: groupLabel(opts, g) || '',
      required: g.getAttribute('aria-required') === 'true', options: opts.map((o) => clean(o.getAttribute('aria-label')) || textOf(o)),
      optionSelectors: opts.map(selectorOf), selector: selectorOf(g), name: g.id || '',
      current: opts.filter((o) => o.getAttribute('aria-checked') === 'true').map(textOf).join(', '),
    });
  });

  // ARIA comboboxes that are not native inputs (div-based React selects)
  ordered(scope, '[role="combobox"]').forEach((el) => {
    if (['input', 'select', 'textarea'].includes(el.tagName.toLowerCase())) return;
    if (!visible(el) || outOfScope(el)) return;
    let label = labelOf(el);
    if (phoneBuddy(el) && !/country/i.test(label)) label = 'Phone country code' + (label ? ` (${label})` : '');
    add(el, {
      ...base(el), kind: 'combobox', label, required: isRequired(el), options: [],
      optionSelectors: [], selector: selectorOf(el), name: el.id || '', current: textOf(el).slice(0, 200),
    });
  });

  // Buttons that open an option list ("Select an option ▾") — Gem, Dover, ...
  const SELECT_TEXT = /^(select|choose|please select|please choose|select an option|select one|choose an option|choose one|--)\b/i;
  const seenCombo = new Set(out.map((o) => o.selector));
  ordered(scope, 'button, [role="button"]').forEach((b) => {
    if (!visible(b) || outOfScope(b)) return;
    if ((b.getAttribute('role') || '').toLowerCase() === 'combobox') return;
    const popup = (b.getAttribute('aria-haspopup') || '').toLowerCase();
    const txt = textOf(b);
    if (!(popup === 'listbox' || (SELECT_TEXT.test(txt) && txt.length < 40))) return;
    const sel = selectorOf(b);
    if (seenCombo.has(sel)) return;
    let label = ownLabel(b);
    if (!label || label === txt) label = precedingText(b) || containerLabel(parentOf(b));
    if (phoneBuddy(b) && !/country/i.test(label)) label = 'Phone country code' + (label ? ` (${label})` : '');
    add(b, {
      ...base(b), kind: 'combobox', label, required: isRequired(b), options: [], optionSelectors: [],
      selector: sel, name: b.id || '', current: SELECT_TEXT.test(txt) ? '' : txt,
    });
  });

  // Yes/No button pairs (no input element at all)
  const seen = new Set();
  ordered(scope, 'button').forEach((btn) => {
    const parent = btn.parentElement;
    if (!parent || seen.has(parent) || !visible(btn)) return;
    const buttons = Array.from(parent.children).filter((c) => c.tagName === 'BUTTON');
    const texts = buttons.map((b) => clean(b.innerText).toLowerCase());
    if (buttons.length !== 2 || !texts.includes('yes') || !texts.includes('no')) return;
    seen.add(parent);
    add(parent, {
      ...base(parent), kind: 'yesno', label: precedingText(parent) || containerLabel(parent.parentElement) || '',
      required: parent.getAttribute('aria-required') === 'true',
      options: buttons.map((b) => clean(b.innerText)), optionSelectors: [],
      selector: selectorOf(parent), name: '',
    });
  });
  out.sort((a, b) => a.order - b.order);
  return out;
}
"""


@dataclass(frozen=True)
class Question:
    """One question on the form, addressed by a CSS selector."""

    kind: str
    label: str
    selector: str
    required: bool = False
    options: tuple[str, ...] = ()
    option_selectors: tuple[str, ...] = ()
    max_length: int | None = None
    input_type: str = ""
    name: str = ""
    accept: str = ""
    context: str = ""  # ancestor ids/classes, lowercased
    placeholder: str = ""
    current: str = ""  # value already in the field (prefilled / default)

    @property
    def qid(self) -> str:
        return self.selector

    @property
    def is_phone_country(self) -> bool:
        return self.label.lower().startswith("phone country code")

    def with_options(self, options: tuple[str, ...]) -> "Question":
        return replace(self, options=options)


def _fold(text: str) -> str:
    """'Résumé' -> 'Resume': accents break the word-level matching rules."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def clean_label(label: str) -> tuple[str, bool]:
    """Strip required markers; return (label, marked_required).

    '*' counts only at the end; Lever's '✱' can sit mid-label
    ("Resume/CV ✱ ATTACH RESUME/CV"). Also understood: "* (required)",
    "* Required" (Teamtailor), "(Required) ..." (Pinpoint), "Required. ...".
    """
    label = _fold(_ZERO_WIDTH.sub("", " ".join((label or "").split())))
    marked = False
    for pattern in (_REQUIRED_WORD_END, _REQUIRED_WORD_START):
        if pattern.search(label):
            marked = True
            label = pattern.sub(" ", label).strip()
    starred = bool(_REQUIRED_MARK.search(label)) or "✱" in label
    label = _REQUIRED_MARK.sub("", label).replace("✱", " ")
    # a star left mid-label by "Question * help text"
    if re.search(r"\s\*\s", f" {label} "):
        starred = True
        label = re.sub(r"\s\*(\s|$)", " ", label)
    return " ".join(label.split()), starred or marked


def questions_from_scan(raw: list[dict]) -> tuple[Question, ...]:
    """Pure: descriptors from _SCAN_JS -> typed Questions (unaddressable ones dropped)."""
    questions = []
    for r in raw:
        if not r.get("selector"):
            continue
        label, starred = clean_label(r.get("label", ""))
        max_len = r.get("maxLength")
        questions.append(Question(
            kind=r["kind"],
            label=label,
            selector=r["selector"],
            required=bool(r.get("required")) or starred,
            options=tuple(o for o in r.get("options", ()) if o),
            option_selectors=tuple(r.get("optionSelectors", ())),
            max_length=int(max_len) if str(max_len or "").isdigit() else None,
            input_type=r.get("inputType", "") or "",
            name=r.get("name", "") or "",
            accept=r.get("accept", "") or "",
            context=r.get("context", "") or "",
            placeholder=r.get("placeholder", "") or "",
            current=r.get("current", "") or "",
        ))
    return tuple(questions)


def scan_form(page) -> tuple[Question, ...]:
    """Enumerate the questions of the application form on the current page."""
    return questions_from_scan(page.evaluate(_SCAN_JS))
