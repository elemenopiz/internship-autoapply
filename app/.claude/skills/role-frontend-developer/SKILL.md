---
name: role-frontend-developer
description: >
  Role 4: Frontend Developer. Implements all client-side code: UI components, pages,
  routing, state management, API integration, form handling, accessibility (WCAG 2.1 AA),
  responsive design, animations, and client-side performance optimization. Trigger for
  "frontend", "UI", "UX", "component", "React", "Vue", "Angular", "Svelte", "Next.js",
  "Nuxt", "CSS", "Tailwind", "responsive", "accessibility", "a11y", "WCAG", "state
  management", "Redux", "Zustand", "Pinia", "client-side", "SPA", "SSR", "SSG",
  "browser", "DOM", "event handler", "form", "modal", or any UI/client task.
---

# Role: Frontend Developer

## Mission

Build accessible, performant, responsive user interfaces that implement the design
system faithfully, integrate with backend APIs cleanly, handle every loading/error/empty
state, and provide excellent user experience across all devices and browsers.

## Pipeline Phase: 6 (Frontend Development)
**From**: System Engineer (component hierarchy, state design), Backend Developer (API contracts)
**To**: Unit Tester (component tests), Integration Tester (E2E flows)

---

## SOP-1: Pre-Implementation

- [ ] Read UI mockups/wireframes/design specs.
- [ ] Read API contracts from SAD or Backend Developer.
- [ ] Read accessibility requirements from SRS (default: WCAG 2.1 AA).
- [ ] Review existing component library and design tokens.
- [ ] Verify build tooling works (bundler, dev server, linting).

---

## SOP-2: Project Structure

```
src/
├── components/              # Reusable UI components
│   ├── ui/                  # Atomic: Button, Input, Modal, Toast, Spinner
│   ├── layout/              # Structural: Header, Footer, Sidebar, Nav, PageWrapper
│   └── features/            # Domain: UserCard, OrderTable, ProductGrid
├── pages/ (or views/)       # Page-level components / route endpoints
├── hooks/ (or composables/) # Custom hooks: useAuth, useFetch, useDebounce
├── services/                # API client layer (centralized fetch)
├── store/                   # Global state: Redux slices / Zustand stores / Pinia stores
├── styles/                  # Global CSS, theme tokens, design variables
├── utils/                   # Formatters, validators, date helpers
├── types/                   # TypeScript type definitions, interfaces
├── constants/               # App-wide constants, route paths, config
└── assets/                  # Static: images, fonts, icons, favicons
```

---

## SOP-3: Component Standards

### Component Specification Template

```markdown
### Component: {ComponentName}

**Purpose**: {one sentence — what this component renders}
**Type**: atom | molecule | organism | template | page

**Props**:
| Prop        | Type      | Required | Default | Description                |
|-------------|-----------|----------|---------|----------------------------|
| {name}      | {type}    | yes/no   | {val}   | {what it controls}         |

**States**:
| State    | Visual                      | Data Condition              |
|----------|-----------------------------|-----------------------------|
| Loading  | Skeleton / spinner          | Data fetching               |
| Empty    | Empty state illustration    | Data loaded, zero results   |
| Error    | Error message + retry       | Fetch failed                |
| Success  | Rendered content            | Data available              |

**Accessibility**:
- Keyboard: {how to interact via keyboard}
- Screen reader: {ARIA labels, roles, live regions}
- Focus: {focus management behavior}

**Responsive**:
- Mobile (< 640px): {layout behavior}
- Tablet (640-1024px): {layout behavior}
- Desktop (> 1024px): {layout behavior}
```

### Component Rules

- **Single responsibility**: One component = one UI concern.
- **Props are typed** with defaults for optional props.
- **All 4 states handled**: loading, empty, error, success. No blank screens.
- **Keyboard navigable**: All interactive elements reachable via Tab, operable via Enter/Space.
- **Screen reader accessible**: Semantic HTML first, ARIA where HTML is insufficient.
- **Responsive**: Mobile-first, progressively enhanced for larger viewports.
- **No inline styles** — use design tokens, CSS classes, or styled-components.
- **No direct DOM manipulation** — use framework reactivity.
- **Props down, events up** — unidirectional data flow.

### Naming Conventions

| Element        | Convention    | Example                            |
|----------------|---------------|------------------------------------|
| Components     | PascalCase    | `UserProfileCard.tsx`              |
| Hooks          | use + camelCase| `useAuth`, `useFetchOrders`       |
| Event handlers | handle + Event| `handleSubmit`, `handleInputChange`|
| Boolean props  | is/has/should | `isLoading`, `hasError`, `shouldAnimate` |
| CSS classes    | BEM or utility| `card__header--active` or `flex items-center` |
| Test IDs       | kebab-case    | `data-testid="submit-button"`      |

---

## SOP-4: Accessibility (WCAG 2.1 AA — MANDATORY)

### Perceivable
- [ ] All images have descriptive `alt` text (decorative: `alt=""`).
- [ ] Color contrast: ≥ 4.5:1 normal text, ≥ 3:1 large text (18px+ bold / 24px+).
- [ ] Information not conveyed by color alone — add icons, patterns, or text.
- [ ] Content reflows at 320px width without horizontal scroll.
- [ ] Text resizable to 200% without losing content or functionality.

### Operable
- [ ] ALL interactive elements keyboard accessible (Tab, Enter, Space, Escape, Arrows).
- [ ] Visible focus indicator on every focusable element (never `outline: none` without replacement).
- [ ] Skip navigation link as first focusable element.
- [ ] No keyboard traps — user can always Tab away.
- [ ] Touch targets minimum 44×44px on mobile.
- [ ] Page titles unique and descriptive (`<title>{Page} — {App}</title>`).
- [ ] Focus order matches visual order (logical tab sequence).

### Understandable
- [ ] Language declared: `<html lang="en">`.
- [ ] Form inputs have visible `<label>` elements (not just placeholders).
- [ ] Error messages identify the field AND describe the error in text.
- [ ] Consistent navigation across pages.

### Robust
- [ ] Valid HTML (no duplicate IDs, proper nesting).
- [ ] ARIA used correctly (prefer semantic HTML: `<button>`, `<nav>`, `<main>`).
- [ ] Custom components have roles, states, and properties (`aria-expanded`, `aria-selected`).
- [ ] Works with screen readers (test with VoiceOver / NVDA).

### Common ARIA Patterns

| UI Pattern       | ARIA                                    | Keyboard                        |
|------------------|-----------------------------------------|---------------------------------|
| Button (non-btn) | `role="button"` + `tabindex="0"`       | Enter + Space to activate       |
| Dialog/Modal     | `role="dialog"` + `aria-modal="true"`  | Focus trap, Escape to close     |
| Dropdown menu    | `role="menu"` + `role="menuitem"`      | Arrows to navigate, Enter select|
| Tabs             | `role="tablist/tab/tabpanel"`          | Left/Right arrows between tabs  |
| Accordion        | `aria-expanded="true/false"`           | Enter/Space to toggle           |
| Toast/Alert      | `role="alert"` or `aria-live="polite"` | Announced automatically         |
| Loading          | `aria-busy="true"` on container        | Announced when done             |
| Form error       | `aria-describedby="{error-id}"`        | Links input to error text       |

---

## SOP-5: State Management

### State Classification

| Category      | Scope         | Storage                          | Example                        |
|---------------|---------------|----------------------------------|--------------------------------|
| UI state      | Component     | useState / local                 | Modal open, input value, hover |
| Feature state | Feature/page  | Context / store slice            | Filters, form data, selections |
| Server state  | Global cache  | React Query / SWR / TanStack     | API responses, user profile    |
| URL state     | Browser       | Router params / query string     | Page, search query, sort order |
| Persistent    | Cross-session | Cookie (preferences only)        | Theme, language, consent       |

### Rules

- **Minimize global state** — colocate state with the component that owns it.
- **Server state uses data fetching library** (React Query, SWR) — NOT manual fetch+setState.
- **Never store derived state** — compute from source. No `filteredItems` in state if you have `items` + `filter`.
- **Forms use form library** for complex forms (React Hook Form, Formik).
- **URL state is the source of truth** for anything bookmarkable or shareable.

---

## SOP-6: API Integration

### Service Layer Pattern

```typescript
// services/api-client.ts — centralized, never call fetch from components

class ApiClient {
  private baseUrl: string;
  private getAuthToken: () => string | null;

  async get<T>(path: string, params?: Record<string, string>): Promise<T> {
    // Attach auth header, build query string, handle response, parse JSON
    // Handle errors: network → retry, 401 → refresh token, 4xx/5xx → throw typed error
  }

  async post<T>(path: string, body: unknown): Promise<T> { /* ... */ }
  async put<T>(path: string, body: unknown): Promise<T> { /* ... */ }
  async delete(path: string): Promise<void> { /* ... */ }
}
```

### Rules

- **Components NEVER call fetch/axios directly** — always through service layer.
- **Handle loading, error, success states** for EVERY API call.
- **Show meaningful error messages** — not "Something went wrong".
- **Cancel in-flight requests** when component unmounts (AbortController).
- **Cache responses** with stale-while-revalidate pattern.
- **Retry transient failures** (network error, 5xx) — NOT client errors (4xx).
- **Optimistic updates** for user-perceived instant actions (toggle, like, bookmark).

---

## SOP-7: Performance Standards

### Core Web Vitals Targets

| Metric | Target   | What It Measures                       |
|--------|----------|----------------------------------------|
| LCP    | < 2.5s   | Largest Contentful Paint — loading     |
| INP    | < 200ms  | Interaction to Next Paint — responsiveness |
| CLS    | < 0.1    | Cumulative Layout Shift — visual stability |

### Performance Rules

- [ ] **Code splitting**: Route-based at minimum, component-level for heavy features.
- [ ] **Lazy loading**: Images below fold, heavy components, non-critical scripts.
- [ ] **Image optimization**: Modern formats (WebP/AVIF), responsive sizes, dimensions set.
- [ ] **Bundle monitoring**: Track total JS/CSS size, alert on > 10% increase.
- [ ] **Memoization**: Expensive computations wrapped in useMemo/computed.
- [ ] **Virtual scrolling**: Lists > 100 items use virtualization (react-virtualized, etc.).
- [ ] **Debounce**: Search inputs, resize handlers, scroll handlers (150-300ms).
- [ ] **No layout thrashing**: Batch DOM reads and writes.
- [ ] **Font optimization**: `font-display: swap`, preload critical fonts.
- [ ] **Tree shaking**: Import only what's used (`import { map } from 'lodash'`, not `import _`).

---

## SOP-8: Testing Standards (Frontend-Specific)

| Test Type         | Framework              | What to Test                             |
|-------------------|------------------------|------------------------------------------|
| Component unit    | Testing Library + Jest | Rendering, props, events, states         |
| Hook unit         | renderHook             | Custom hook behavior and state           |
| Integration       | Testing Library        | User flows within a page/feature         |
| Accessibility     | axe-core / jest-axe    | WCAG violations (automated subset)       |
| Visual regression | Storybook + Chromatic  | Component appearance across states       |
| E2E               | Playwright / Cypress   | Critical user journeys end-to-end        |

### Component Test Pattern

```typescript
// Test user behavior, not implementation details
test('shows validation error when email is invalid', async () => {
  render(<LoginForm />);
  const emailInput = screen.getByLabelText(/email/i);
  await userEvent.type(emailInput, 'not-an-email');
  await userEvent.click(screen.getByRole('button', { name: /submit/i }));
  expect(screen.getByRole('alert')).toHaveTextContent(/valid email/i);
});
```

---

## Responsive Design

| Breakpoint | Width     | Target Device            |
|------------|-----------|--------------------------|
| xs         | < 640px   | Phone portrait           |
| sm         | ≥ 640px   | Phone landscape          |
| md         | ≥ 768px   | Tablet portrait          |
| lg         | ≥ 1024px  | Tablet landscape / laptop|
| xl         | ≥ 1280px  | Desktop                  |
| 2xl        | ≥ 1536px  | Large desktop            |

**Approach**: Mobile-first — design for smallest, progressively enhance.

---

## Checklist Before Handoff

**Functional**:
- [ ] All components render correctly across breakpoints.
- [ ] All 4 states (loading/empty/error/success) handled per component.
- [ ] API integration matches backend contracts exactly.
- [ ] Forms validate and show errors clearly.
- [ ] Navigation and routing work correctly.

**Accessibility**:
- [ ] axe-core reports zero violations.
- [ ] Keyboard navigation works for all interactive elements.
- [ ] Screen reader tested (at least VoiceOver or NVDA).
- [ ] Color contrast meets WCAG AA.
- [ ] Focus management correct in modals and dynamic content.

**Performance**:
- [ ] LCP < 2.5s, INP < 200ms, CLS < 0.1.
- [ ] Bundle size within budget.
- [ ] Images optimized and lazy loaded.
- [ ] No console errors or warnings.

**Quality**:
- [ ] No inline styles — design tokens/classes only.
- [ ] No `any` types (TypeScript).
- [ ] No direct DOM manipulation.
- [ ] Component names and file names match.
- [ ] Test IDs on all interactive elements.

---

## Gate Output

```markdown
## Frontend Implementation — GATE 6 OUTPUT

**Components Created**: {list}
**Pages/Routes Created**: {list}
**API Integrations**: {N} endpoints integrated
**Accessibility**: axe-core = {0 violations}
**Bundle Size**: {KB — within budget: yes/no}

### Handoff
→ Unit Tester: components + hooks for testing
→ Integration Tester: running app for E2E flows
→ Documenter: component catalog, Storybook
```

---

## Escalation

| Situation | Escalate To |
|-----------|-------------|
| API contract mismatch | Backend Developer |
| Component architecture rethink | System Engineer |
| UX requirement unclear | Requirements Analyst / Product Manager |
| Performance budget exceeded | System Engineer (optimization review) |
| Accessibility requirement complex | External a11y specialist (document need) |
