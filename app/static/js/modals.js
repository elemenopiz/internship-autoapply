/* ═══════════════════════════════════════════════════════════════
   MODALS — with focus trap and ARIA management (WCAG 2.1 §2.4.3)
   ═══════════════════════════════════════════════════════════════ */

let _previouslyFocused = null;

const FOCUSABLE_SELECTOR = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function openModal(id) {
  _previouslyFocused = document.activeElement;
  const modal = document.getElementById(id);
  modal.classList.remove('hidden');
  // Focus first focusable element inside the dialog box
  const dialog = modal.querySelector('[role="dialog"], [role="alertdialog"]') || modal.querySelector('.modal-box');
  if (dialog) {
    const first = dialog.querySelector(FOCUSABLE_SELECTOR);
    if (first) setTimeout(() => first.focus(), 50);
  }
}

export function closeModal(id) {
  document.getElementById(id).classList.add('hidden');
  // Return focus to the element that opened the modal
  if (_previouslyFocused && typeof _previouslyFocused.focus === 'function') {
    _previouslyFocused.focus();
    _previouslyFocused = null;
  }
}

// Close modals on overlay click
document.addEventListener('click', e => {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.classList.add('hidden');
    if (_previouslyFocused && typeof _previouslyFocused.focus === 'function') {
      _previouslyFocused.focus();
      _previouslyFocused = null;
    }
  }
});

// Close modals on Escape
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    const openModals = document.querySelectorAll('.modal-overlay:not(.hidden)');
    openModals.forEach(m => m.classList.add('hidden'));
    if (openModals.length && _previouslyFocused && typeof _previouslyFocused.focus === 'function') {
      _previouslyFocused.focus();
      _previouslyFocused = null;
    }
  }

  // Focus trap: keep Tab within open modal dialog
  if (e.key === 'Tab') {
    const openModal = document.querySelector('.modal-overlay:not(.hidden)');
    if (!openModal) return;
    const dialog = openModal.querySelector('[role="dialog"], [role="alertdialog"]') || openModal.querySelector('.modal-box');
    if (!dialog) return;
    const focusable = [...dialog.querySelectorAll(FOCUSABLE_SELECTOR)];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }
});
