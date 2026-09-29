# Internationalization (i18n)

## Architecture

AutoApply supports full internationalization with a JSON-based locale system:

```
┌─────────────────────────────────┐
│  static/locales/en.json         │  ◀── String catalog (383 keys, 23 sections)
└───────────┬─────────────────────┘
            │
    ┌───────┴───────┐
    │               │
    ▼               ▼
┌────────┐    ┌──────────┐
│Backend │    │ Frontend │
│core/   │    │ static/  │
│i18n.py │    │ js/      │
│        │    │ i18n.js  │
│ t()    │    │ t()      │
└────────┘    └──────────┘
```

- **Locale files**: JSON files in `static/locales/` (e.g., `en.json`).
- **Backend translation**: `core/i18n.py` provides a `t()` function for Python code.
- **Frontend translation**: `static/js/i18n.js` provides a `t()` function for JavaScript code.
- **HTML auto-translation**: `data-i18n` attributes on HTML elements are auto-translated when the locale changes.
- **No build step**: Locale files are served as static assets. Adding a new language requires no code changes.

---

## String Catalog

The English locale file (`static/locales/en.json`) contains **383 keys** organized into **23 sections**:

| Section | Key Prefix | Example Keys | Description |
|---------|-----------|--------------|-------------|
| `common` | `common.*` | `common.save`, `common.cancel`, `common.delete` | Shared UI labels |
| `nav` | `nav.*` | `nav.dashboard`, `nav.applications`, `nav.settings` | Navigation items |
| `dashboard` | `dashboard.*` | `dashboard.title`, `dashboard.bot_status` | Dashboard tab |
| `applications` | `applications.*` | `applications.title`, `applications.no_results` | Applications tab |
| `settings` | `settings.*` | `settings.title`, `settings.profile` | Settings tab |
| `wizard` | `wizard.*` | `wizard.welcome`, `wizard.step_profile` | Setup wizard |
| `bot` | `bot.*` | `bot.start`, `bot.stop`, `bot.status_idle` | Bot controls |
| `review` | `review.*` | `review.approve`, `review.reject`, `review.skip` | Review mode |
| `analytics` | `analytics.*` | `analytics.total`, `analytics.today` | Analytics panel |
| `feed` | `feed.*` | `feed.job_found`, `feed.job_applied` | Live feed messages |
| `errors` | `errors.*` | `errors.not_found`, `errors.unauthorized` | Error messages |
| `validation` | `validation.*` | `validation.required`, `validation.email` | Form validation |
| `status` | `status.*` | `status.applied`, `status.interview` | Application statuses |
| `platforms` | `platforms.*` | `platforms.linkedin`, `platforms.indeed` | Platform names |
| `schedule` | `schedule.*` | `schedule.enabled`, `schedule.start_time` | Scheduler UI |
| `profile` | `profile.*` | `profile.full_name`, `profile.email` | Profile fields |
| `search` | `search.*` | `search.job_titles`, `search.locations` | Search criteria |
| `llm` | `llm.*` | `llm.provider`, `llm.api_key` | AI provider config |
| `export` | `export.*` | `export.csv`, `export.downloading` | Export features |
| `login` | `login.*` | `login.open_browser`, `login.close_browser` | Platform login |
| `a11y` | `a11y.*` | `a11y.skip_to_main`, `a11y.loading` | Accessibility labels |
| `confirm` | `confirm.*` | `confirm.delete`, `confirm.stop_bot` | Confirmation dialogs |
| `toast` | `toast.*` | `toast.saved`, `toast.error` | Toast notifications |

---

## Backend Usage

### Import

```python
from core.i18n import t
```

### Basic Translation

```python
message = t("errors.not_found")
# Returns: "Not found"
```

### Placeholder Interpolation

String placeholders use `{placeholder}` syntax:

```python
message = t("feed.job_found", title="Software Engineer", company="Acme Corp")
# Template: "Found: {title} at {company}"
# Returns: "Found: Software Engineer at Acme Corp"
```

### In Flask Routes

```python
from core.i18n import t

@bp.route('/api/example/<id>')
def get_example(id):
    item = db.find(id)
    if not item:
        return jsonify({"error": t("errors.not_found")}), 404
    return jsonify(item)
```

### Missing Keys

If a key is not found in the locale file, `t()` returns the key itself as a fallback:

```python
t("nonexistent.key")
# Returns: "nonexistent.key"
```

---

## Frontend Usage

### Import

```javascript
import { t } from './i18n.js';
```

### Basic Translation

```javascript
const title = t('dashboard.title');
// Returns: "Dashboard"
```

### Placeholder Interpolation

```javascript
const message = t('feed.job_applied', { title: 'Software Engineer', company: 'Acme Corp' });
// Template: "Applied to {title} at {company}"
// Returns: "Applied to Software Engineer at Acme Corp"
```

### In DOM Manipulation

```javascript
document.getElementById('status').textContent = t('bot.status_idle');
```

---

## HTML Attributes

HTML elements can be tagged for automatic translation using `data-i18n` attributes:

### Text Content

```html
<h1 data-i18n="dashboard.title">Dashboard</h1>
```

### Placeholder Text

```html
<input data-i18n-placeholder="search.placeholder" placeholder="Search jobs...">
```

### ARIA Labels

```html
<button data-i18n-aria-label="a11y.close_dialog" aria-label="Close dialog">X</button>
```

### Title Attribute

```html
<span data-i18n-title="common.tooltip_info" title="More information">i</span>
```

### Multiple Attributes on One Element

```html
<input
  data-i18n-placeholder="search.placeholder"
  data-i18n-aria-label="search.aria_label"
  placeholder="Search..."
  aria-label="Search applications"
>
```

---

## Auto-Translation: `_applyDataI18n()`

When the locale changes, the `_applyDataI18n()` function automatically translates all tagged elements in the DOM:

```javascript
// Called internally when locale changes
function _applyDataI18n() {
  // Translate text content
  document.querySelectorAll('[data-i18n]').forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });

  // Translate placeholders
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });

  // Translate aria-labels
  document.querySelectorAll('[data-i18n-aria-label]').forEach(el => {
    el.setAttribute('aria-label', t(el.dataset.i18nAriaLabel));
  });

  // Translate title attributes
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    el.title = t(el.dataset.i18nTitle);
  });
}
```

This function is called automatically when:
- The application loads for the first time.
- The user switches to a different locale.
- A new section of the UI is rendered dynamically.

---

## Adding a New Language

1. **Copy the English locale file**:
   ```bash
   cp static/locales/en.json static/locales/es.json
   ```

2. **Translate all values** in the new file. Keep the keys identical:
   ```json
   {
     "common": {
       "save": "Guardar",
       "cancel": "Cancelar",
       "delete": "Eliminar"
     }
   }
   ```

3. **Done**. The new locale automatically appears in the `GET /api/locales` response and can be selected in the UI.

No code changes, no configuration updates, no build step required.

---

## Adding New Strings

1. **Add the key** to `static/locales/en.json` in the appropriate section:
   ```json
   {
     "dashboard": {
       "new_feature_label": "My New Feature"
     }
   }
   ```

2. **Use in backend code**:
   ```python
   from core.i18n import t
   label = t("dashboard.new_feature_label")
   ```

3. **Use in frontend code**:
   ```javascript
   import { t } from './i18n.js';
   const label = t('dashboard.new_feature_label');
   ```

4. **Use in HTML**:
   ```html
   <span data-i18n="dashboard.new_feature_label">My New Feature</span>
   ```

5. **Add translations** to other locale files (e.g., `es.json`). If a key is missing from a locale file, the English fallback is used.

---

## Testing

i18n functionality is tested in `tests/test_i18n.py` with **21 tests** covering:

| Test Area | Description |
|-----------|-------------|
| Key resolution | `t()` returns correct string for valid keys |
| Missing keys | `t()` returns key name as fallback |
| Placeholders | `{placeholder}` interpolation works correctly |
| Nested keys | Dot-notation key resolution (`section.subsection.key`) |
| Locale loading | JSON files parsed correctly |
| Locale listing | `GET /api/locales` returns available locales |
| Section completeness | All sections present in en.json |
| Key count | Validates expected number of keys |

Run i18n tests:

```bash
pytest tests/test_i18n.py -v
```
