# SRS + SAD — TASK-013: Frontend Component Refactor (LE-1)

**Created**: 2026-03-10
**Phase**: Production Readiness Phase D
**Score Impact**: +0.5 (8.0 → 8.5)

---

## 1. Requirements (SRS)

### Functional Requirements

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| FR-LE1-01 | Extract all CSS from index.html into `static/css/main.css` | Given the app loads, When inspecting sources, Then CSS is served from `/static/css/main.css` |
| FR-LE1-02 | Extract all JS into ES modules under `static/js/` | Given the app loads, When inspecting sources, Then JS is loaded as ES modules from `/static/js/` |
| FR-LE1-03 | Create a module per feature area: auth, state, socket, navigation, wizard, bot-control, feed, applications, profile, settings, analytics, review, modals, tag-input, file-upload, helpers | Given a developer opens any module, Then it contains only code for that feature |
| FR-LE1-04 | index.html contains only HTML structure + Jinja2 token injection + module entry point | Given the template is read, Then it has <link> to CSS, <script type="module"> to app.js, and Jinja2 `{{ api_token }}` |
| FR-LE1-05 | All existing functionality works identically after refactor | Given any user action, When performed, Then the result is identical to pre-refactor |

### Non-Functional Requirements

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-LE1-01 | No build step required | ES modules served directly by Flask |
| NFR-LE1-02 | No framework dependencies added | Vanilla JS only |
| NFR-LE1-03 | All 339 existing tests pass | Zero regressions |
| NFR-LE1-04 | Page load time not degraded >10% | Module count < 20 (minimal HTTP requests) |

---

## 2. Architecture (SAD)

### ADR-017: Frontend Module Architecture

**Decision**: Split monolithic index.html into ES modules served from `static/js/` and CSS from `static/css/`. No bundler. Flask serves static files natively.

**Rationale**:
- ES modules (`type="module"`) are natively supported by all modern browsers and Electron's Chromium
- No build step keeps the project simple
- Flask already has static file serving configured (default `static/` folder)
- Auth bypass for `/static` already exists in `app.py`

**Rejected Alternatives**:
- Vite/Webpack bundler — adds build complexity for a desktop app
- Preact/Alpine.js — adds framework dependency, rewrite required
- Web Components — over-engineering for this use case

### File Structure

```
static/
├── css/
│   └── main.css                    # All CSS (~825 lines)
└── js/
    ├── app.js                      # Entry point: imports all modules, DOMContentLoaded init
    ├── state.js                    # Global state object + getters/setters
    ├── auth.js                     # fetch override with Bearer token
    ├── socket.js                   # Socket.IO connection + handlers
    ├── navigation.js               # Tab switching, screen loading
    ├── wizard.js                   # 7-step setup wizard
    ├── bot-control.js              # Start/pause/stop, uptime timer
    ├── feed.js                     # Live feed + history
    ├── applications.js             # Table, pagination, detail modal, CSV export
    ├── profile.js                  # Experience files CRUD
    ├── settings.js                 # Settings form load/save, LLM config
    ├── analytics.js                # Chart.js visualizations
    ├── review.js                   # Review card actions
    ├── modals.js                   # Modal open/close utilities
    ├── tag-input.js                # Tag input component
    ├── file-upload.js              # Drag-drop resume upload
    └── helpers.js                  # escHtml, escAttr, matchColor, badgeClass
```

### Module Dependency Graph

```
app.js (entry)
  ├── auth.js          (standalone — runs on import)
  ├── state.js         (standalone — exports state object)
  ├── helpers.js       (standalone — exports utility functions)
  ├── modals.js        (imports: helpers)
  ├── tag-input.js     (imports: state)
  ├── file-upload.js   (imports: state)
  ├── socket.js        (imports: state, feed, review, bot-control)
  ├── navigation.js    (imports: state, feed, applications, profile, analytics, settings)
  ├── wizard.js        (imports: state, tag-input, file-upload, navigation)
  ├── bot-control.js   (imports: state)
  ├── feed.js          (imports: state, helpers)
  ├── applications.js  (imports: state, helpers, modals)
  ├── profile.js       (imports: state, helpers, modals)
  ├── settings.js      (imports: state, tag-input)
  ├── analytics.js     (imports: state)
  └── review.js        (imports: state)
```

### Template Changes (index.html)

**Before** (~3,170 lines): All HTML + CSS + JS inline
**After** (~900 lines): HTML only + 3 lines for assets:
```html
<link rel="stylesheet" href="/static/css/main.css">
<script>window.__apiToken = "Bearer {{ api_token }}";</script>
<script type="module" src="/static/js/app.js"></script>
```

### Migration Strategy

1. Extract CSS verbatim → `static/css/main.css`
2. Extract JS functions into modules by feature area
3. Convert global function calls to imports/exports
4. Replace inline event handlers (`onclick="fn()"`) with `addEventListener` in modules
5. Update index.html to HTML-only + asset links
6. Test each screen manually + run full test suite

---

## 3. Traceability

| Req ID | Design Ref | Source Files | Tests | Status |
|--------|-----------|--------------|-------|--------|
| FR-LE1-01 | ADR-017 | static/css/main.css | Visual verification | ⚠️ |
| FR-LE1-02 | ADR-017 | static/js/*.js | Visual verification | ⚠️ |
| FR-LE1-03 | ADR-017 | static/js/*.js | Module count check | ⚠️ |
| FR-LE1-04 | ADR-017 | templates/index.html | Template size check | ⚠️ |
| FR-LE1-05 | ADR-017 | All | 339 existing tests | ⚠️ |
