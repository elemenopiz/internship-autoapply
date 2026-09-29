/* ═══════════════════════════════════════════════════════════════
   RESUME LIBRARY — browse, preview, download, favorite, compare
   Implements: FR-115 (library UI), FR-116 (detail view), FR-117 (app link),
               FR-120 (favorite toggle), FR-123 (comparison view)
   ═══════════════════════════════════════════════════════════════ */
import { apiFetch, escHtml } from './api.js';
import { t, _applyDataI18n } from './i18n.js';

let currentPage = 1;
let currentPerPage = 50;
let searchTimeout = null;

/** IDs selected for comparison (max 2) */
let compareSelection = [];

/* ── Metrics cards ─────────────────────────────────────────── */

async function loadResumeMetrics() {
  const container = document.getElementById('resume-metrics');
  if (!container) return;
  try {
    const data = await apiFetch('/api/resumes/metrics');
    container.innerHTML = `
      <div class="summary-card" role="group" aria-label="${escHtml(t('resumes.total_versions'))}">
        <div class="summary-value">${data.total_versions}</div>
        <div class="summary-label" data-i18n="resumes.total_versions">${escHtml(t('resumes.total_versions'))}</div>
      </div>
      <div class="summary-card" role="group" aria-label="${escHtml(t('resumes.tailored_rate'))}">
        <div class="summary-value">${data.tailored_interview_rate}%</div>
        <div class="summary-label" data-i18n="resumes.tailored_rate">${escHtml(t('resumes.tailored_rate'))}</div>
      </div>
      <div class="summary-card" role="group" aria-label="${escHtml(t('resumes.fallback_rate'))}">
        <div class="summary-value">${data.fallback_interview_rate}%</div>
        <div class="summary-label" data-i18n="resumes.fallback_rate">${escHtml(t('resumes.fallback_rate'))}</div>
      </div>
      <div class="summary-card" role="group" aria-label="${escHtml(t('resumes.avg_score_interview'))}">
        <div class="summary-value">${data.avg_score_interviewed}</div>
        <div class="summary-label" data-i18n="resumes.avg_score_interview">${escHtml(t('resumes.avg_score_interview'))}</div>
      </div>
    `;
    _applyDataI18n(container);
  } catch {
    container.innerHTML = '';
  }
}

/* ── Compare selection helpers ────────────────────────────── */

function updateCompareUI() {
  const btn = document.getElementById('resume-compare-btn');
  const info = document.getElementById('resume-compare-info');
  if (btn) btn.disabled = compareSelection.length < 2;
  if (info) {
    info.textContent = compareSelection.length > 0
      ? t('resumes.compare_select_info').replace('{count}', compareSelection.length)
      : t('resumes.compare_select');
  }
  // Sync checkbox states
  document.querySelectorAll('.resume-compare-check').forEach(cb => {
    cb.checked = compareSelection.includes(parseInt(cb.value, 10));
  });
}

function onCompareCheckChange(id) {
  const numId = parseInt(id, 10);
  const idx = compareSelection.indexOf(numId);
  if (idx >= 0) {
    compareSelection.splice(idx, 1);
  } else {
    if (compareSelection.length >= 2) {
      // Remove oldest selection
      compareSelection.shift();
    }
    compareSelection.push(numId);
  }
  updateCompareUI();
}

/* ── Resume list ───────────────────────────────────────────── */

export async function loadResumes(page) {
  if (page !== undefined) currentPage = page;

  const listEl = document.getElementById('resume-list');
  const paginationEl = document.getElementById('resume-pagination');
  if (!listEl) return;

  const searchInput = document.getElementById('resume-search');
  const sortSelect = document.getElementById('resume-sort');
  const search = searchInput ? searchInput.value.trim() : '';
  const sort = sortSelect ? sortSelect.value : 'created_at';

  listEl.innerHTML = `<p class="text-muted" data-i18n="resumes.loading">${escHtml(t('resumes.loading'))}</p>`;

  try {
    const params = new URLSearchParams({
      page: currentPage, per_page: currentPerPage, sort, order: 'desc',
    });
    if (search) params.set('search', search);

    const data = await apiFetch(`/api/resumes?${params}`);
    loadResumeMetrics();

    if (!data.items || data.items.length === 0) {
      listEl.innerHTML = `<p class="analytics-empty" data-i18n="resumes.empty">${escHtml(t('resumes.empty'))}</p>`;
      if (paginationEl) paginationEl.innerHTML = '';
      _applyDataI18n(listEl);
      return;
    }

    const rows = data.items.map(item => {
      const isFav = !!item.is_favorite;
      const starLabel = isFav ? t('resumes.unfavorite') : t('resumes.favorite');
      const starCls = isFav ? 'resume-star active' : 'resume-star';
      const isChecked = compareSelection.includes(item.id) ? 'checked' : '';

      return `
      <tr>
        <td class="no-row-click">
          <input type="checkbox" class="resume-compare-check" value="${item.id}"
                 onchange="onCompareCheck(${item.id})"
                 aria-label="${escHtml(t('resumes.compare'))} ${escHtml(item.company)}"
                 ${isChecked}>
        </td>
        <td class="no-row-click">
          <button type="button" class="${starCls}" onclick="toggleFavorite(${item.id})"
                  aria-label="${escHtml(starLabel)}" aria-pressed="${isFav}"
                  title="${escHtml(starLabel)}">&#9733;</button>
        </td>
        <td>${escHtml(item.company)}</td>
        <td>${escHtml(item.job_title)}</td>
        <td>${item.match_score ?? '-'}</td>
        <td>${escHtml((item.created_at || '').slice(0, 10))}</td>
        <td><span class="status-mini-badge status-${escHtml(item.application_status || 'unknown')}">${escHtml(item.application_status || '-')}</span></td>
        <td>${escHtml(item.llm_provider || t('resumes.no_provider'))}</td>
        <td>
          <button type="button" class="btn btn-sm btn-primary" onclick="viewResume(${item.id})"
                  aria-label="${escHtml(t('resumes.view'))} ${escHtml(item.company)}"
                  data-i18n="resumes.view">${escHtml(t('resumes.view'))}</button>
          ${item.resume_pdf_exists ? `<button type="button" class="btn btn-sm btn-secondary" onclick="downloadResume(${item.id})"
                  aria-label="${escHtml(t('resumes.download'))} ${escHtml(item.company)}"
                  data-i18n="resumes.download">${escHtml(t('resumes.download'))}</button>` : ''}
        </td>
      </tr>`;
    }).join('');

    listEl.innerHTML = `
      <table class="analytics-table" role="table" aria-label="${escHtml(t('resumes.title'))}">
        <thead>
          <tr>
            <th style="width:36px" aria-label="${escHtml(t('resumes.compare'))}"></th>
            <th style="width:36px" aria-label="${escHtml(t('resumes.favorite'))}"></th>
            <th data-i18n="resumes.col_company">${escHtml(t('resumes.col_company'))}</th>
            <th data-i18n="resumes.col_job_title">${escHtml(t('resumes.col_job_title'))}</th>
            <th data-i18n="resumes.col_score">${escHtml(t('resumes.col_score'))}</th>
            <th data-i18n="resumes.col_date">${escHtml(t('resumes.col_date'))}</th>
            <th data-i18n="resumes.col_status">${escHtml(t('resumes.col_status'))}</th>
            <th data-i18n="resumes.col_provider">${escHtml(t('resumes.col_provider'))}</th>
            <th data-i18n="resumes.col_actions">${escHtml(t('resumes.col_actions'))}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
    _applyDataI18n(listEl);
    updateCompareUI();

    // Pagination
    const totalPages = Math.ceil(data.total_count / currentPerPage);
    if (paginationEl && totalPages > 1) {
      const pageInfo = t('resumes.page_info')
        .replace('{page}', currentPage)
        .replace('{total}', totalPages);
      paginationEl.innerHTML = `
        <button type="button" class="btn btn-sm btn-secondary" onclick="switchResumePage(${currentPage - 1})"
                ${currentPage <= 1 ? 'disabled' : ''} data-i18n="resumes.prev_page">${escHtml(t('resumes.prev_page'))}</button>
        <span class="resume-page-info">${escHtml(pageInfo)}</span>
        <button type="button" class="btn btn-sm btn-secondary" onclick="switchResumePage(${currentPage + 1})"
                ${currentPage >= totalPages ? 'disabled' : ''} data-i18n="resumes.next_page">${escHtml(t('resumes.next_page'))}</button>
      `;
      _applyDataI18n(paginationEl);
    } else if (paginationEl) {
      paginationEl.innerHTML = '';
    }
  } catch {
    listEl.innerHTML = `<p class="text-danger" data-i18n="resumes.error_loading">${escHtml(t('resumes.error_loading'))}</p>`;
    _applyDataI18n(listEl);
  }
}

/* ── Favorite toggle ─────────────────────────────────────── */

export async function toggleFavorite(id) {
  try {
    await apiFetch(`/api/resumes/${id}/favorite`, { method: 'PUT' });
    loadResumes();
  } catch {
    // Silently fail — user sees no change
  }
}

/* ── Comparison view ─────────────────────────────────────── */

export async function compareSelected() {
  if (compareSelection.length < 2) return;
  const [leftId, rightId] = compareSelection;

  const overlay = document.getElementById('resume-compare-overlay');
  const content = document.getElementById('resume-compare-content');
  if (!overlay || !content) return;

  content.innerHTML = `<p data-i18n="resumes.loading">${escHtml(t('resumes.loading'))}</p>`;
  overlay.classList.remove('hidden');

  try {
    const data = await apiFetch(`/api/resumes/compare?left=${leftId}&right=${rightId}`);
    const left = data.left;
    const right = data.right;

    const leftMissing = !left.resume_md_content;
    const rightMissing = !right.resume_md_content;

    let diffHtml = '';
    if (leftMissing || rightMissing) {
      diffHtml = `<p class="text-muted" data-i18n="resumes.compare_file_missing">${escHtml(t('resumes.compare_file_missing'))}</p>`;
    } else {
      diffHtml = renderLineDiff(left.resume_md_content, right.resume_md_content);
    }

    content.innerHTML = `
      <div class="resume-compare-side">
        <div class="resume-compare-side-header">
          <strong>${escHtml(left.company)}</strong> — ${escHtml(left.job_title)}
          <span class="text-muted">${escHtml((left.created_at || '').slice(0, 10))}</span>
        </div>
      </div>
      <div class="resume-compare-side">
        <div class="resume-compare-side-header">
          <strong>${escHtml(right.company)}</strong> — ${escHtml(right.job_title)}
          <span class="text-muted">${escHtml((right.created_at || '').slice(0, 10))}</span>
        </div>
      </div>
      <div class="resume-compare-diff" role="region" aria-label="${escHtml(t('resumes.compare_title'))}">
        ${diffHtml}
      </div>
    `;
    _applyDataI18n(content);
  } catch {
    content.innerHTML = `<p class="text-danger" data-i18n="resumes.error_loading">${escHtml(t('resumes.error_loading'))}</p>`;
    _applyDataI18n(content);
  }
}

export function closeCompareView() {
  const overlay = document.getElementById('resume-compare-overlay');
  if (overlay) overlay.classList.add('hidden');
}

/* ── Line diff (LCS-based, client-side — ADR-024) ────────── */

function computeLCS(a, b) {
  const m = a.length;
  const n = b.length;
  // Use rolling 2-row DP for space efficiency, then backtrack
  const dp = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1));
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = a[i - 1] === b[j - 1]
        ? dp[i - 1][j - 1] + 1
        : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }
  // Backtrack to get diff operations
  const ops = [];
  let i = m, j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i - 1] === b[j - 1]) {
      ops.push({ type: 'equal', line: a[i - 1] });
      i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      ops.push({ type: 'added', line: b[j - 1] });
      j--;
    } else {
      ops.push({ type: 'removed', line: a[i - 1] });
      i--;
    }
  }
  return ops.reverse();
}

function renderLineDiff(leftText, rightText) {
  const leftLines = leftText.split('\n');
  const rightLines = rightText.split('\n');
  const ops = computeLCS(leftLines, rightLines);

  const html = ops.map(op => {
    const cls = op.type === 'added' ? 'diff-added'
      : op.type === 'removed' ? 'diff-removed'
      : 'diff-unchanged';
    const prefix = op.type === 'added' ? '+ '
      : op.type === 'removed' ? '- '
      : '  ';
    return `<div class="${cls}" role="text">${escHtml(prefix + op.line)}</div>`;
  }).join('');

  return `<div class="diff-output" role="log" aria-label="Line diff">${html}</div>`;
}

/* ── Detail view ───────────────────────────────────────────── */

export async function viewResume(id) {
  const overlay = document.getElementById('resume-detail-overlay');
  const content = document.getElementById('resume-detail-content');
  if (!overlay || !content) return;

  content.innerHTML = `<p data-i18n="resumes.loading">${escHtml(t('resumes.loading'))}</p>`;
  overlay.classList.remove('hidden');

  try {
    const data = await apiFetch(`/api/resumes/${id}`);

    const mdContent = data.resume_md_content
      ? escHtml(data.resume_md_content).replace(/\n/g, '<br>')
      : '';

    content.innerHTML = `
      <div class="resume-detail-meta">
        <div class="resume-meta-item">
          <strong data-i18n="resumes.col_company">${escHtml(t('resumes.col_company'))}</strong>
          <span>${escHtml(data.company)}</span>
        </div>
        <div class="resume-meta-item">
          <strong data-i18n="resumes.col_job_title">${escHtml(t('resumes.col_job_title'))}</strong>
          <span>${escHtml(data.job_title)}</span>
        </div>
        <div class="resume-meta-item">
          <strong data-i18n="resumes.match_score">${escHtml(t('resumes.match_score'))}</strong>
          <span>${data.match_score ?? '-'}</span>
        </div>
        <div class="resume-meta-item">
          <strong data-i18n="resumes.generated_on">${escHtml(t('resumes.generated_on'))}</strong>
          <span>${escHtml((data.created_at || '').slice(0, 10))}</span>
        </div>
        <div class="resume-meta-item">
          <strong data-i18n="resumes.ai_model">${escHtml(t('resumes.ai_model'))}</strong>
          <span>${escHtml(data.llm_provider || '')} ${escHtml(data.llm_model || '')}</span>
        </div>
        <div class="resume-meta-item">
          <strong data-i18n="resumes.col_status">${escHtml(t('resumes.col_status'))}</strong>
          <span class="status-mini-badge status-${escHtml(data.application_status || 'unknown')}">${escHtml(data.application_status || '-')}</span>
        </div>
      </div>

      <div class="resume-detail-actions">
        ${data.resume_pdf_exists ? `
          <button type="button" class="btn btn-primary" onclick="previewResumePdf(${id})"
                  data-i18n="resumes.view_pdf">${escHtml(t('resumes.view_pdf'))}</button>
          <button type="button" class="btn btn-secondary" onclick="downloadResume(${id})"
                  data-i18n="resumes.download_pdf">${escHtml(t('resumes.download_pdf'))}</button>
        ` : `<p class="text-muted" data-i18n="resumes.file_missing">${escHtml(t('resumes.file_missing'))}</p>`}
        ${data.application_id ? `
          <button type="button" class="btn btn-secondary" onclick="closeResumeDetail(); window.viewApplication && window.viewApplication(${data.application_id})"
                  data-i18n="resumes.view_application">${escHtml(t('resumes.view_application'))}</button>
        ` : ''}
      </div>

      ${data.file_missing ? `<p class="text-danger" data-i18n="resumes.file_missing">${escHtml(t('resumes.file_missing'))}</p>` : ''}

      <div id="resume-pdf-container" class="resume-pdf-embed hidden"></div>

      ${mdContent ? `
        <div class="resume-detail-content">
          <h3 data-i18n="resumes.metadata">${escHtml(t('resumes.metadata'))}</h3>
          <div class="resume-md-rendered">${mdContent}</div>
        </div>
      ` : ''}
    `;
    _applyDataI18n(content);
  } catch {
    content.innerHTML = `<p class="text-danger" data-i18n="resumes.error_loading">${escHtml(t('resumes.error_loading'))}</p>`;
    _applyDataI18n(content);
  }
}

export function closeResumeDetail() {
  const overlay = document.getElementById('resume-detail-overlay');
  if (overlay) overlay.classList.add('hidden');
  const pdfContainer = document.getElementById('resume-pdf-container');
  if (pdfContainer) {
    pdfContainer.innerHTML = '';
    pdfContainer.classList.add('hidden');
  }
}

export function previewResumePdf(id) {
  const container = document.getElementById('resume-pdf-container');
  if (!container) return;
  container.innerHTML = `<iframe src="/api/resumes/${id}/pdf" title="Resume PDF preview"
    style="width:100%;height:600px;border:1px solid var(--border-color,#333);border-radius:8px;"
    ></iframe>`;
  container.classList.remove('hidden');
}

export function downloadResume(id) {
  const link = document.createElement('a');
  link.href = `/api/resumes/${id}/pdf?download=true`;
  link.download = `resume_${id}.pdf`;
  link.click();
}

export function switchResumePage(page) {
  if (page < 1) return;
  loadResumes(page);
}

export { onCompareCheckChange as onCompareCheck };

/* ── Init search listener ──────────────────────────────────── */

export function initResumeSearch() {
  const searchInput = document.getElementById('resume-search');
  const sortSelect = document.getElementById('resume-sort');

  if (searchInput) {
    searchInput.addEventListener('input', () => {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(() => loadResumes(1), 300);
    });
  }
  if (sortSelect) {
    sortSelect.addEventListener('change', () => loadResumes(1));
  }
}
