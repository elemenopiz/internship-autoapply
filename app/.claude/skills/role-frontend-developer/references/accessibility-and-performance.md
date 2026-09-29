# Frontend Reference: Accessibility & Performance

## WCAG 2.1 AA Compliance Checklist

### Perceivable
- [ ] All images have descriptive `alt` text (decorative: `alt=""`)
- [ ] Video/audio has captions and transcripts
- [ ] Color contrast: 4.5:1 normal text, 3:1 large text (18px+ bold or 24px+)
- [ ] Information not conveyed by color alone (add icons, patterns, text)
- [ ] Content reflows at 320px width without horizontal scroll
- [ ] Text resizable to 200% without loss of content

### Operable
- [ ] All interactive elements keyboard accessible (Tab, Enter, Space, Escape, Arrow)
- [ ] Visible focus indicator on all focusable elements
- [ ] Skip navigation link for screen readers
- [ ] No keyboard traps (can Tab away from any element)
- [ ] Touch targets minimum 44×44px
- [ ] No content flashes more than 3 times per second
- [ ] Page titles are unique and descriptive
- [ ] Focus order matches visual order

### Understandable
- [ ] Language declared: `<html lang="en">`
- [ ] Form inputs have visible labels (not just placeholders)
- [ ] Error messages identify the field and describe the error in text
- [ ] Consistent navigation across pages
- [ ] No unexpected context changes on focus or input

### Robust
- [ ] Valid HTML (no duplicate IDs, proper nesting)
- [ ] ARIA used correctly (prefer semantic HTML first)
- [ ] Custom components have appropriate roles and states
- [ ] Works with screen readers (VoiceOver, NVDA, JAWS)

## ARIA Quick Reference
| Need | ARIA | Example |
|------|------|---------|
| Button (non-button element) | `role="button"` + keyboard | `<div role="button" tabindex="0">` |
| Navigation landmark | `role="navigation"` or `<nav>` | Prefer `<nav aria-label="Main">` |
| Live updates | `aria-live="polite"` | Toast notifications, search results |
| Loading state | `aria-busy="true"` | Skeleton loaders, data fetching |
| Expanded/collapsed | `aria-expanded="true/false"` | Accordions, dropdowns |
| Current page | `aria-current="page"` | Active nav link |
| Required field | `aria-required="true"` or `required` | Form inputs |
| Error description | `aria-describedby="error-id"` | Link input to error message |
| Modal dialog | `role="dialog"` + `aria-modal="true"` | Focus trap required |
| Tab interface | `role="tablist/tab/tabpanel"` | Arrow key navigation |

## Core Web Vitals Targets & Fixes

### LCP (Largest Contentful Paint) < 2.5s
| Problem | Fix |
|---------|-----|
| Large hero image | Compress, use WebP/AVIF, add `loading="eager"` + `fetchpriority="high"` |
| Render-blocking CSS/JS | Inline critical CSS, defer non-critical |
| Slow server response | CDN, caching, edge rendering (SSR/ISR) |
| Web fonts blocking | `font-display: swap`, preload critical fonts |

### FID / INP (First Input Delay / Interaction to Next Paint) < 200ms
| Problem | Fix |
|---------|-----|
| Long main thread tasks | Break into chunks with `requestIdleCallback` or `setTimeout` |
| Heavy JS bundle | Code-split, tree-shake, lazy-load routes |
| Expensive event handlers | Debounce/throttle, use `passive: true` for scroll/touch |
| Hydration blocking | Progressive hydration, islands architecture |

### CLS (Cumulative Layout Shift) < 0.1
| Problem | Fix |
|---------|-----|
| Images without dimensions | Always set `width` and `height` attributes |
| Ads/embeds without reserved space | Use `aspect-ratio` or min-height placeholder |
| Dynamic content injected above viewport | Insert below fold or use `content-visibility` |
| Web fonts causing reflow | `font-display: optional` or size-adjust |

## Component Testing Patterns

```jsx
// Test user behavior, not implementation
// GOOD: Tests what user sees and does
test('shows error when submitting empty form', async () => {
  render(<LoginForm />);
  await userEvent.click(screen.getByRole('button', { name: /submit/i }));
  expect(screen.getByRole('alert')).toHaveTextContent(/email is required/i);
});

// BAD: Tests implementation details
test('sets error state', () => {
  const { result } = renderHook(() => useForm());
  act(() => result.current.validate());
  expect(result.current.errors.email).toBe('required'); // fragile!
});
```

## Responsive Breakpoints (Common)
| Name | Width | Typical Device |
|------|-------|----------------|
| xs | < 640px | Phone portrait |
| sm | ≥ 640px | Phone landscape |
| md | ≥ 768px | Tablet portrait |
| lg | ≥ 1024px | Tablet landscape / small laptop |
| xl | ≥ 1280px | Desktop |
| 2xl | ≥ 1536px | Large desktop |

Design mobile-first: start with smallest, add complexity for larger.
