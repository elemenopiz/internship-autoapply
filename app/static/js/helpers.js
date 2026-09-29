/* ═══════════════════════════════════════════════════════════════
   HELPERS
   ═══════════════════════════════════════════════════════════════ */

export function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

export function escAttr(s) {
  return String(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function matchColor(pct) {
  if (pct == null) return 'var(--text-dim)';
  if (pct >= 80) return 'var(--success)';
  if (pct >= 60) return 'var(--warning)';
  return 'var(--danger)';
}

export function badgeClass(eventType) {
  const map = { APPLIED: 'success', ERROR: 'danger', CAPTCHA: 'danger', FILTERED: 'dim', FOUND: 'info', GENERATING: 'info', APPLYING: 'info', REVIEW: 'review', SKIPPED: 'dim' };
  return map[eventType] || 'info';
}
