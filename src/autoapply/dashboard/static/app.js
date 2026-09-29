/* AutoApply dashboard. Vanilla JS, no dependencies, no inline handlers (the CSP forbids them).
   All user-visible data is inserted as text or element values, never parsed as HTML. */
(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const csrfToken = () => (($('meta[name="csrf-token"]') || {}).content || "");

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "text") node.textContent = value;
      else if (key === "value") node.value = value;
      else node.setAttribute(key, value);
    }
    for (const child of children) node.append(child);
    return node;
  }

  // ---------------------------------------------------------------- API
  class ApiError extends Error {
    constructor(status, body) {
      super((body && body.message) || `Request failed (${status})`);
      this.status = status;
      this.body = body || {};
    }
  }

  async function api(method, url, body, isForm = false) {
    const headers = { Accept: "application/json" };
    const options = { method, headers, credentials: "same-origin" };
    if (method !== "GET") headers["X-CSRF-Token"] = csrfToken();
    if (body !== undefined) {
      if (isForm) {
        options.body = body;
      } else {
        headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(body);
      }
    }
    let response;
    try {
      response = await fetch(url, options);
    } catch (error) {
      throw new ApiError(0, { message: "Could not reach the dashboard server." });
    }
    let data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }
    if (!response.ok) throw new ApiError(response.status, data);
    return data;
  }

  function describeError(error) {
    if (!(error instanceof ApiError)) return [String(error && error.message ? error.message : error)];
    const body = error.body || {};
    const lines = [];
    if (Array.isArray(body.errors)) {
      for (const item of body.errors) {
        const where = (item.loc || []).filter((part) => part !== "body").join(".");
        lines.push(where ? `${where}: ${item.msg}` : item.msg);
      }
    }
    if (Array.isArray(body.issues)) {
      for (const issue of body.issues) lines.push(`${issue.field}: ${issue.message}`);
    }
    if (!lines.length) lines.push(error.message);
    return lines;
  }

  // ---------------------------------------------------------------- messages
  let flashTimer = null;
  function flash(message, kind = "info") {
    const box = $("#flash");
    if (!box) return;
    box.textContent = message;
    box.className = `flash flash-${kind}`;
    box.hidden = false;
    clearTimeout(flashTimer);
    if (kind !== "error") flashTimer = setTimeout(() => { box.hidden = true; }, 8000);
  }

  function setStatus(form, text, kind = "info") {
    const node = $("[data-status]", form);
    if (node) {
      node.textContent = text;
      node.className = `form-status status-${kind}`;
    }
  }

  function clearErrors(form) {
    $$(".field-error", form).forEach((node) => node.remove());
    $$("[aria-invalid]", form).forEach((node) => node.removeAttribute("aria-invalid"));
    const list = $("[data-errors]", form);
    if (list) list.replaceChildren();
  }

  function findField(form, loc) {
    const parts = (loc || []).filter((part) => typeof part === "string" && part !== "body");
    for (let count = parts.length; count > 0; count--) {
      const name = parts.slice(0, count).join(".");
      const found = $$("[name]", form).find((node) => node.name === name);
      if (found) return found;
    }
    return null;
  }

  function showFormErrors(form, error) {
    const body = error instanceof ApiError ? error.body || {} : {};
    const leftovers = [];
    if (Array.isArray(body.errors)) {
      for (const item of body.errors) {
        const field = findField(form, item.loc);
        if (field) {
          field.setAttribute("aria-invalid", "true");
          const holder = field.closest(".field") || field.parentElement;
          holder.append(el("small", { class: "field-error", text: item.msg }));
        } else {
          const where = (item.loc || []).filter((part) => part !== "body").join(".");
          leftovers.push(where ? `${where}: ${item.msg}` : item.msg);
        }
      }
    } else {
      leftovers.push(...describeError(error));
    }
    if (Array.isArray(body.issues)) {
      for (const issue of body.issues) leftovers.push(`${issue.field}: ${issue.message}`);
    }
    const list = $("[data-errors]", form);
    if (list) for (const line of leftovers) list.append(el("li", { text: line }));
    setStatus(form, error instanceof ApiError && error.status === 422 ? "Please fix the highlighted fields." : (error.message || "Failed."), "error");
  }

  // ---------------------------------------------------------------- forms
  function setPath(target, path, value) {
    const keys = path.split(".");
    let node = target;
    for (const key of keys.slice(0, -1)) {
      if (typeof node[key] !== "object" || node[key] === null) node[key] = {};
      node = node[key];
    }
    node[keys[keys.length - 1]] = value;
  }

  function serialize(form) {
    const out = {};
    for (const node of $$("[name]", form)) {
      if (node.closest("template") || node.type === "file") continue;
      const kind = node.dataset.type || "";
      let value;
      if (node.type === "checkbox") {
        if (kind === "multi-int") {
          const current = node.name.split(".").reduce((acc, key) => (acc ? acc[key] : undefined), out);
          if (!Array.isArray(current)) setPath(out, node.name, []);
          if (node.checked) node.name.split(".").reduce((acc, key) => acc[key], out).push(Number(node.value));
          continue;
        }
        value = node.checked;
      } else if (kind === "tristate") {
        value = node.value === "" ? null : node.value === "true";
      } else if (kind === "int" || kind === "float") {
        value = node.value.trim() === "" ? null : Number(node.value);
      } else if (kind === "lines") {
        value = node.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
      } else {
        value = node.value;
      }
      setPath(out, node.name, value);
    }
    return out;
  }

  function collectFamilies(form) {
    const families = {};
    for (const box of $$("[data-family]", form)) {
      const name = $('[data-k="name"]', box).value.trim();
      const weight = $('[data-k="weight"]', box).value;
      const keywords = $('[data-k="keywords"]', box).value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
      if (!name) throw new Error("Every role family needs a name.");
      if (name in families) throw new Error(`Role family "${name}" appears twice.`);
      families[name] = { keywords, weight: weight === "" ? 1 : Number(weight) };
    }
    return families;
  }

  async function saveWithGates(method, url, body) {
    try {
      return await api(method, url, body);
    } catch (error) {
      if (error instanceof ApiError && error.body.code === "dry_run_recommended") {
        const ok = window.confirm(`${error.message}\n\nEnable it anyway?`);
        if (ok) return api(method, url, { ...body, acknowledge_no_dry_run: true });
      }
      throw error;
    }
  }

  async function submitForm(form) {
    clearErrors(form);
    let body;
    try {
      body = serialize(form);
      if (form.hasAttribute("data-search-form")) body.role_families = collectFamilies(form);
    } catch (error) {
      setStatus(form, error.message, "error");
      return;
    }
    setStatus(form, "Saving...");
    try {
      await saveWithGates(form.dataset.method || "POST", form.dataset.api, body);
      setStatus(form, "Saved.", "ok");
      if (form.hasAttribute("data-reload")) location.reload();
    } catch (error) {
      showFormErrors(form, error);
    }
  }

  async function submitUpload(form) {
    clearErrors(form);
    const input = $('input[type="file"]', form);
    if (!input.files.length) {
      setStatus(form, "Choose a PDF first.", "error");
      return;
    }
    const data = new FormData();
    data.append("file", input.files[0]);
    setStatus(form, "Uploading...");
    try {
      const result = await api("POST", form.dataset.upload, data, true);
      setStatus(form, "Uploaded.", "ok");
      const line = $("#resume-status");
      if (line) line.textContent = `Present: ${result.filename}, ${result.size} bytes.`;
    } catch (error) {
      showFormErrors(form, error);
    }
  }

  // ---------------------------------------------------------------- overview state
  function pick(object, path) {
    return path.split(".").reduce((acc, key) => (acc === null || acc === undefined ? acc : acc[key]), object);
  }

  function renderIssues(issues) {
    const list = $("#issue-list");
    if (!list) return;
    list.replaceChildren();
    for (const issue of issues) {
      const item = el("li", { "data-code": issue.code }, el("strong", { text: issue.field }), document.createTextNode(`: ${issue.message} `));
      if (issue.link && issue.link.startsWith("/")) item.append(el("a", { href: issue.link, text: "Fix it" }));
      list.append(item);
    }
  }

  function applyState(state) {
    for (const node of $$("[data-bind]")) {
      const value = pick(state, node.dataset.bind);
      node.textContent = value === null || value === undefined || value === "" ? "none" : String(value);
    }
    const bar = $("#cap-bar");
    if (bar) {
      bar.max = Math.max(state.daily_cap, 1);
      bar.value = state.submitted_today;
    }
    const running = $("[data-bind-running]");
    if (running) running.textContent = state.running ? "Running" : "Idle";
    const keyNode = $("[data-bind-key]");
    if (keyNode) {
      const key = state.secrets.openai_key;
      keyNode.textContent = key.present ? `present (${key.source})` : "missing";
    }
    const stop = $("#stop-banner");
    if (stop) stop.hidden = !state.stop_file;
    const ok = $("#ready-ok");
    const issues = $("#ready-issues");
    if (ok && issues) {
      ok.hidden = !state.readiness.ok;
      issues.hidden = state.readiness.ok;
      renderIssues(state.readiness.issues);
    }
    const counts = $("#status-counts");
    if (counts) {
      counts.replaceChildren();
      for (const [status, count] of Object.entries(state.applications_by_status)) {
        counts.append(el("tr", {}, el("td", { text: status.replace(/_/g, " ") }), el("td", { text: String(count) })));
      }
    }
    const toggle = $("#schedule-toggle");
    if (toggle && document.activeElement !== toggle) toggle.checked = !!state.schedule.enabled;
  }

  async function refreshState() {
    if (!$("#cap-bar")) return;
    try {
      applyState(await api("GET", "/api/state"));
    } catch (error) {
      /* transient; the next poll retries */
    }
  }

  // ---------------------------------------------------------------- knowledge base editor
  const csv = (text) => text.split(",").map((s) => s.trim()).filter(Boolean);
  const lines = (text) => text.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);

  function addExperience(data = {}) {
    const holder = $("#kb-experiences");
    const node = $("#exp-template").content.firstElementChild.cloneNode(true);
    const set = (key, value) => { $(`[data-k="${key}"]`, node).value = value; };
    set("id", data.id || "");
    set("kind", data.kind || "work");
    set("title", data.title || "");
    set("organization", data.organization || "");
    set("location", data.location || "");
    set("start", data.start || "");
    set("end", data.end || "");
    set("skills", (data.skills || []).join(", "));
    set("bullets", (data.bullets || []).join("\n"));
    set("links", (data.links || []).join("\n"));
    holder.append(node);
    return node;
  }

  function renderKb(kb) {
    const form = $("#kb-form");
    form.dataset.source = kb.source || "none";
    $("[data-kb-skills]", form).value = (kb.skills || []).join(", ");
    $("#kb-experiences").replaceChildren();
    for (const exp of kb.experiences || []) addExperience(exp);
  }

  function collectKb() {
    const form = $("#kb-form");
    const get = (node, key) => $(`[data-k="${key}"]`, node).value;
    return {
      source: form.dataset.source || "none",
      skills: csv($("[data-kb-skills]", form).value),
      experiences: $$(".experience", form.parentElement).map((node) => ({
        id: get(node, "id").trim(),
        kind: get(node, "kind"),
        title: get(node, "title").trim(),
        organization: get(node, "organization").trim(),
        location: get(node, "location").trim(),
        start: get(node, "start").trim(),
        end: get(node, "end").trim(),
        skills: csv(get(node, "skills")),
        bullets: lines(get(node, "bullets")),
        links: lines(get(node, "links")),
      })),
    };
  }

  function initKb() {
    const form = $("#kb-form");
    if (!form) return;
    let kb = { source: "none", experiences: [], skills: [] };
    try {
      kb = JSON.parse(form.dataset.kb) || kb;
    } catch (error) {
      /* an unreadable knowledge base starts as an empty editor */
    }
    renderKb(kb);
  }

  // ---------------------------------------------------------------- workbook inspector
  function renderInspection(report) {
    const box = $("#inspect-result");
    box.replaceChildren();
    box.append(el("p", { text: report.sheet ? `Using sheet "${report.sheet}", header on row ${report.header_row}. ${report.kept ?? 0} of ${report.data_rows ?? 0} rows would be kept.` : "No usable sheet or header row was found." }));
    if (Array.isArray(report.sheets) && report.sheets.length) box.append(el("p", { class: "muted", text: `Sheets: ${report.sheets.join(", ")}` }));
    const mapping = report.mapping || {};
    if (Object.keys(mapping).length) {
      const table = el("table", { class: "table" }, el("thead", {}, el("tr", {}, el("th", { text: "Field" }), el("th", { text: "Column header" }))));
      const body = el("tbody");
      for (const [field, header] of Object.entries(mapping)) body.append(el("tr", {}, el("td", { text: field }), el("td", { text: String(header) })));
      table.append(body);
      box.append(table);
    }
    for (const warning of report.warnings || []) box.append(el("p", { class: "warn", text: String(warning) }));
    const sample = report.sample_rows || [];
    if (sample.length) {
      const keys = Object.keys(sample[0]);
      const table = el("table", { class: "table" }, el("thead", {}, el("tr", {}, ...keys.map((k) => el("th", { text: k })))));
      const body = el("tbody");
      for (const row of sample) body.append(el("tr", {}, ...keys.map((k) => el("td", { text: String(row[k] ?? "") }))));
      table.append(body);
      box.append(el("div", { class: "table-wrap" }, table));
    }
  }

  // ---------------------------------------------------------------- actions
  function addFamily() {
    const holder = $("#families");
    const box = el("fieldset", { class: "family", "data-family": "" }, el("legend", { text: "Family" }));
    const grid = el("div", { class: "grid" },
      el("div", { class: "field" }, el("label", { text: "Name" }), el("input", { "data-k": "name", maxlength: "60" })),
      el("div", { class: "field" }, el("label", { text: "Weight (0-1)" }), el("input", { "data-k": "weight", type: "number", min: "0", max: "1", step: "0.05", value: "1" })));
    box.append(grid,
      el("div", { class: "field" }, el("label", { text: "Keywords (one per line)" }), el("textarea", { "data-k": "keywords", rows: "4" })),
      el("button", { type: "button", class: "btn btn-danger", "data-action": "remove-family", text: "Remove family" }));
    holder.append(box);
  }

  function runFailure(error) {
    const lines = describeError(error);
    flash(lines.join(" | "), "error");
    if (error instanceof ApiError && Array.isArray(error.body.issues)) renderIssues(error.body.issues);
  }

  const actions = {
    async run(button) {
      const mode = button.dataset.mode;
      try {
        const result = await api("POST", "/api/run", mode ? { mode } : {});
        flash(`Run started (${result.mode}).`, "ok");
        refreshState();
      } catch (error) {
        runFailure(error);
      }
    },
    async stop() {
      try {
        await api("POST", "/api/stop");
        flash("STOP switch on: no new application will start.", "ok");
        const banner = $("#stop-banner");
        if (banner) banner.hidden = false;
        refreshState();
      } catch (error) {
        runFailure(error);
      }
    },
    async unstop() {
      try {
        await api("POST", "/api/unstop");
        flash("STOP switch cleared.", "ok");
        const banner = $("#stop-banner");
        if (banner) banner.hidden = true;
        refreshState();
      } catch (error) {
        runFailure(error);
      }
    },
    async "mark-applied"(button) {
      try {
        await api("POST", `/api/applications/${encodeURIComponent(button.dataset.opportunityId)}/mark-applied`);
        location.reload();
      } catch (error) {
        runFailure(error);
      }
    },
    "edit-answer"(button) {
      toggleEdit(button.closest("tr"), true);
    },
    "cancel-edit"(button) {
      toggleEdit(button.closest("tr"), false);
    },
    async "save-answer"(button) {
      const row = button.closest("tr");
      const body = {};
      for (const node of $$("[data-field]", row)) body[node.dataset.field] = node.value;
      try {
        await api("PUT", `/api/answers/${row.dataset.answerId}`, body);
        location.reload();
      } catch (error) {
        runFailure(error);
      }
    },
    async "delete-answer"(button) {
      const row = button.closest("tr");
      if (!window.confirm("Delete this saved answer?")) return;
      try {
        await api("DELETE", `/api/answers/${row.dataset.answerId}`);
        row.remove();
        flash("Answer deleted.", "ok");
      } catch (error) {
        runFailure(error);
      }
    },
    async "resolve-question"(button) {
      const item = button.closest("[data-question-id]");
      const answer = $("[data-answer-input]", item).value;
      try {
        await api("POST", `/api/pending-questions/${item.dataset.questionId}/resolve`, { answer });
        item.remove();
        flash("Answer saved; it will be reused on later applications.", "ok");
      } catch (error) {
        setStatus(item, describeError(error).join(" "), "error");
      }
    },
    "add-experience"() {
      addExperience().scrollIntoView({ block: "center" });
    },
    "remove-experience"(button) {
      button.closest(".experience").remove();
    },
    async "build-kb"(button) {
      const note = $("#kb-note");
      button.disabled = true;
      note.textContent = "Reading your resume...";
      try {
        const result = await api("POST", "/api/kb/from-resume");
        renderKb(result.kb);
        note.textContent = "Proposed from your resume. Review and edit it, then press Save knowledge base. Nothing is saved yet.";
      } catch (error) {
        note.textContent = describeError(error).join(" ");
      } finally {
        button.disabled = false;
      }
    },
    "add-family"() {
      addFamily();
    },
    "remove-family"(button) {
      button.closest("[data-family]").remove();
    },
    async "inspect-workbook"() {
      const form = $('[name="workbook.path"]').form;
      const path = $('[name="workbook.path"]').value.trim();
      const box = $("#inspect-result");
      box.textContent = "Inspecting...";
      try {
        renderInspection(await api("POST", "/api/workbook/inspect", path ? { path } : {}));
      } catch (error) {
        box.replaceChildren(el("p", { class: "warn", text: describeError(error).join(" ") }));
      }
      return form;
    },
  };

  function toggleEdit(row, editing) {
    $$(".view", row).forEach((node) => { node.hidden = editing; });
    $$(".edit", row).forEach((node) => { node.hidden = !editing; });
  }

  // ---------------------------------------------------------------- wiring
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-action]");
    if (!button || !actions[button.dataset.action]) return;
    event.preventDefault();
    actions[button.dataset.action](button);
  });

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.dataset.upload) {
      event.preventDefault();
      submitUpload(form);
    } else if (form.hasAttribute("data-kb-form")) {
      event.preventDefault();
      saveKb(form);
    } else if (form.dataset.api) {
      event.preventDefault();
      submitForm(form);
    }
  });

  async function saveKb(form) {
    clearErrors(form);
    setStatus(form, "Saving...");
    try {
      const saved = await api("PUT", form.dataset.kbForm, collectKb());
      renderKb(saved);
      setStatus(form, "Saved.", "ok");
    } catch (error) {
      showFormErrors(form, error);
    }
  }

  document.addEventListener("change", async (event) => {
    const node = event.target;
    if (!(node instanceof HTMLElement) || !node.dataset.setting) return;
    const patch = {};
    const previous = node.type === "checkbox" ? !node.checked : node.dataset.previous;
    setPath(patch, node.dataset.setting, node.type === "checkbox" ? node.checked : node.value);
    try {
      await saveWithGates("PUT", "/api/settings", patch);
      flash("Saved.", "ok");
      if (node.type !== "checkbox") node.dataset.previous = node.value;
      refreshState();
    } catch (error) {
      if (node.type === "checkbox") node.checked = previous;
      else if (previous !== undefined) node.value = previous;
      runFailure(error);
    }
  });

  document.addEventListener("DOMContentLoaded", () => {
    $$("[data-setting]").forEach((node) => { if (node.type !== "checkbox") node.dataset.previous = node.value; });
    initKb();
    if (document.body.dataset.page === "overview") {
      setInterval(() => { if (!document.hidden) refreshState(); }, 5000);
    }
  });
})();
