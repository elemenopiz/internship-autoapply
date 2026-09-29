/* ═══════════════════════════════════════════════════════════════
   AUTH — inject Bearer token on all /api/ requests
   ═══════════════════════════════════════════════════════════════ */

const _apiToken = window.__apiToken || '';
const _origFetch = window.fetch;
window.fetch = function(url, opts = {}) {
  if (typeof url === 'string' && url.startsWith('/api/')) {
    opts.headers = opts.headers || {};
    if (opts.headers instanceof Headers) {
      opts.headers.set('Authorization', 'Bearer ' + _apiToken);
    } else {
      opts.headers['Authorization'] = 'Bearer ' + _apiToken;
    }
  }
  return _origFetch.call(this, url, opts);
};
