/* GSA Gateway v2 dashboard — Checkpoint A (shell + Overview).
   Vanilla JS. Loads a .db via sql.js (WASM), reads it locally, renders Tab 1.
   Never queries the vec0 `knowledge_vectors` table (sql.js can't load sqlite-vec);
   embedding coverage comes from the plain `knowledge_vectors_rowids` shadow table. */

let SQL = null;   // sql.js module
let db = null;    // loaded database

const POST_ICONS = {
  one_time: "📢", recurring_instance: "🔁", event_announcement: "📅",
  event_reminder: "⏰", mathcafe: "☕", worldcup: "⚽",
  broadcast: "📣", digest: "📰",
};
const iconFor = (t) => POST_ICONS[t] || "📝";

// ───────── sql.js bootstrap ─────────
initSqlJs({
  locateFile: (f) => `https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.2/${f}`,
}).then((mod) => {
  SQL = mod;
  const params = new URLSearchParams(location.search);
  const srv = params.get("server");
  const url = params.get("db");
  if (srv) {
    connectToServer(srv);  // deep-link: ?server=http://localhost:5555
  } else if (url) {
    fetch(url).then((r) => r.arrayBuffer())
      .then((buf) => loadDatabaseBytes(new Uint8Array(buf), url.split("/").pop()))
      .catch((err) => { console.error("Autoload failed:", err); showLoadError("Autoload failed: " + err.message); });
  } else if (location.protocol.startsWith("http")) {
    // Served by local_server.py? Auto-connect to its own origin (silent on fail).
    connectToServer(location.origin, true);
  }
});

let currentDbName = "gsa_gateway.db";
let dirty = false;

function loadDatabaseBytes(bytes, name) {
  try {
    db = new SQL.Database(bytes);
    // sql.js has no FTS5; drop the knowledge_fts sync triggers so KB writes work.
    window.PostsLogic.prepareForDashboard(db);
    currentDbName = name || "gsa_gateway.db";
    onDbLoaded(name);
  } catch (err) {
    console.error("Open failed:", err);
    showLoadError("Could not open database: " + err.message);
  }
}

// Non-blocking error surface (alert() blocks headless Chrome and is poor UX).
function showLoadError(msg) {
  const es = document.getElementById("empty-state");
  if (es) { es.hidden = false; es.innerHTML = `<div class="empty-card"><div class="empty-icon">⚠️</div><h2>Something went wrong</h2><p>${msg}</p></div>`; }
}

// Persistence is now per-change SQL patches, not a global db save.
function markDirty() { /* no-op: changes are exported as SQL patches */ }
function clearDirty() { /* no-op */ }
function toast(msg, ok = true) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast" + (ok ? " ok" : "");
  t.hidden = false;
  clearTimeout(t._t);
  t._t = setTimeout(() => (t.hidden = true), 2800);
}

// Fix 2: the dashboard never writes the live db. Every change produces a SQL
// patch the admin applies via `sqlite3 gsa_gateway.db < changes.sql`. The old
// full-db "Save" download is removed (it would clobber the bot's open WAL db).
const PL = window.PostsLogic;

function showPatchModal(title, sql, opts = {}) {
  const applyCmd = opts.rebuild
    ? "sqlite3 gsa_gateway.db < changes.sql && python v2/scripts/rebuild_index.py"
    : "sqlite3 gsa_gateway.db < changes.sql";
  document.getElementById("modal-body").innerHTML = `
    <h2>✅ ${esc(title)}</h2>
    <p class="muted">Apply to the live database from your terminal — the dashboard does not write the live file directly.</p>
    <pre class="patch-sql">${esc(sql)}</pre>
    <div class="inline" style="gap:8px;margin:10px 0">
      <button class="btn btn-ghost btn-sm" id="patch-copy">📋 Copy SQL</button>
      <button class="btn btn-ghost btn-sm" id="patch-dl">⬇ Download changes.sql</button>
    </div>
    ${opts.rebuild ? `<p class="muted">Then run <code>python v2/scripts/rebuild_index.py</code> to make it searchable.</p>` : ""}
    <div class="section-label">Run in terminal</div>
    <div class="reindex-row"><code>${esc(applyCmd)}</code><button class="btn btn-ghost btn-sm" id="patch-copycmd">Copy command</button></div>
    <div class="modal-actions"><button class="btn btn-primary" id="patch-close">Close</button></div>`;
  document.getElementById("modal").hidden = false;
  const copy = (text, m) => { if (navigator.clipboard) navigator.clipboard.writeText(text); toast(m); };
  document.getElementById("patch-copy").onclick = () => copy(sql, "SQL copied");
  document.getElementById("patch-copycmd").onclick = () => copy(applyCmd, "Command copied");
  document.getElementById("patch-dl").onclick = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([sql], { type: "text/plain" }));
    a.download = "changes.sql"; a.click(); URL.revokeObjectURL(a.href);
  };
  document.getElementById("patch-close").onclick = closeModal;
}

// Apply simple mutations to the in-memory db (for UI) AND surface them as a patch.
function applyAndExport(innerSql, title, opts = {}) {
  if (SERVER_URL && opts.server) return postToServer(opts.server, opts);
  const tz = PL.orgTimezone(db);
  const patch = `-- GSA Gateway v2 change patch\n-- Generated: ${PL.utcToLocal(PL.nowUTC(), tz, true)}\n` +
    `-- Type: ${opts.type || "update"}\n\nBEGIN TRANSACTION;\n${innerSql}\nCOMMIT;\n`;
  db.exec(patch);
  showPatchModal(title, patch, opts);
}

// ── server mode (local_server.py over SSH tunnel) ─────────────────────────
let SERVER_URL = null;
function isServerMode() { return !!SERVER_URL; }

function serverFetch(path, opts = {}) {
  return fetch(SERVER_URL + path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  }).then((r) => r.json());
}

function postToServer(server, opts = {}) {
  serverFetch(server.path, { method: server.method || "POST", body: server.body })
    .then((res) => {
      if (res && res.success === false) { toast(res.error || "Server error", false); return; }
      toast("Applied to live database ✅" + (opts.rebuild ? " — run rebuild_index.py" : ""));
      reloadFromServer();
    })
    .catch((e) => toast("Server error: " + e.message, false));
}

// server write → POST; file mode (or no server endpoint) → SQL patch.
function applyCreate(server, patch, title, opts = {}, refresh) {
  if (SERVER_URL && server) return postToServer(server, opts);
  db.exec(patch);
  if (refresh) refresh();
  showPatchModal(title, patch, opts);
}

function reloadFromServer() {
  return fetch(SERVER_URL + "/db").then((r) => r.arrayBuffer()).then((buf) => {
    db = new SQL.Database(new Uint8Array(buf));
    PL.prepareForDashboard(db);
    const active = document.querySelector(".nav-item.active");
    if (active) switchTab(active.dataset.tab);
  });
}

function connectToServer(url, auto = false) {
  const base = url.replace(/\/+$/, "");
  fetch(base + "/health").then((r) => r.json()).then((h) => {
    if (h.status !== "ok") throw new Error("server reports not ok");
    SERVER_URL = base;
    return fetch(base + "/db").then((r) => r.arrayBuffer());
  }).then((buf) => {
    db = new SQL.Database(new Uint8Array(buf));
    PL.prepareForDashboard(db);
    currentDbName = base;
    onDbLoaded(base);
    const st = document.getElementById("db-status");
    st.innerHTML = '<span style="color:#7ee2a8">● server (read/write)</span>';
    document.getElementById("db-name").textContent = base;
  }).catch((e) => {
    if (auto) { console.warn("auto-connect skipped:", e.message); }  // leave the choice screen
    else { showLoadError("Could not connect to server: " + e.message); }
  });
}

// ───────── query helpers ─────────
function query(sql, params = []) {
  const stmt = db.prepare(sql);
  if (params.length) stmt.bind(params);
  const rows = [];
  while (stmt.step()) rows.push(stmt.getAsObject());
  stmt.free();
  return rows;
}
function one(sql, params = []) { return query(sql, params)[0] || null; }
function scalar(sql, params = []) {
  try { const r = one(sql, params); return r ? Object.values(r)[0] : null; }
  catch (e) { return null; }
}

// ───────── time formatting ─────────
function orgTz() { return db ? window.PostsLogic.orgTimezone(db) : "America/New_York"; }
function parseTs(s) {
  if (!s) return null;
  // All stored timestamps are UTC: ISO (with offset/Z) parse as-is; bare
  // "YYYY-MM-DD HH:MM:SS" is UTC, so append Z.
  const d = s.includes("T") ? new Date(s) : new Date(s.replace(" ", "T") + "Z");
  return isNaN(d) ? null : d;
}
function relTime(s) {
  const d = parseTs(s); if (!d) return "—";
  let sec = Math.round((Date.now() - d.getTime()) / 1000);
  const future = sec < 0; sec = Math.abs(sec);
  const fmt = (n, u) => future ? `in ${n} ${u}${n > 1 ? "s" : ""}` : `${n} ${u}${n > 1 ? "s" : ""} ago`;
  if (sec < 60) return future ? "soon" : "just now";
  const mins = Math.round(sec / 60); if (mins < 60) return fmt(mins, "min");
  const hrs = Math.round(mins / 60); if (hrs < 24) return fmt(hrs, "hour");
  const days = Math.round(hrs / 24); if (days < 30) return fmt(days, "day");
  return d.toLocaleDateString();
}
function absTime(s) {
  if (!s) return "—";
  return window.PostsLogic.utcToLocal(s, orgTz(), true); // UTC → org-local, e.g. "Jun 12, 6:00 PM EDT"
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const preview = (s, n = 50) => { s = (s || "").replace(/\s+/g, " ").trim(); return s.length > n ? s.slice(0, n) + "…" : s; };

// ───────── file loading ─────────
function wireFilePicker(el) {
  if (!el) return;
  el.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    SERVER_URL = null;
    const reader = new FileReader();
    reader.onload = () => {
      if (!SQL) { toast("sql.js is still loading — try again in a second.", false); return; }
      loadDatabaseBytes(new Uint8Array(reader.result), file.name);
    };
    reader.readAsArrayBuffer(file);
  });
}
wireFilePicker(document.getElementById("db-file"));
wireFilePicker(document.getElementById("db-file2"));
document.getElementById("server-connect").addEventListener("click", () => {
  if (!SQL) { toast("sql.js is still loading — try again.", false); return; }
  connectToServer(document.getElementById("server-url").value.trim());
});

function onDbLoaded(name) {
  document.getElementById("empty-state").hidden = true;
  document.getElementById("db-name").textContent = name;
  const status = document.getElementById("db-status");
  status.textContent = "● " + name;
  status.classList.add("loaded");
  // deep-link conveniences: ?tab=posts&new=1
  const params = new URLSearchParams(location.search);
  const tab = params.get("tab") || "overview";
  switchTab(tab);
  if (tab === "posts") {
    const nv = params.get("new");
    const pv = params.get("post");
    if (nv) openNewPostForm(nv === "1" ? "one_time" : nv);
    else if (pv) { PS.selectedId = Number(pv); PS.view = "detail"; renderPostsList(); renderRightPane(); }
  }
  if (tab === "kb") {
    const ko = params.get("korg"); if (ko) KB.orgId = Number(ko);
    const ki = params.get("kb");
    if (ki) { KB.selectedId = Number(ki); KB.view = params.get("kbview") === "history" ? "history" : "detail"; }
    if (ko || ki) renderKB();
  }
  if (tab === "settings") { const sc = params.get("setcat"); if (sc) { SET.cat = sc; renderSettings(); } }
}

// ───────── tab nav ─────────
const TITLES = { overview: "Overview", posts: "Posts", kb: "Knowledge Base",
  org: "Organization", people: "People (Knowledge Graph)", analytics: "Analytics",
  settings: "Settings", jobs: "Data Sources", judging: "Judging" };

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

function switchTab(tab) {
  if (!db) return;
  document.querySelectorAll(".nav-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".tab").forEach((t) => (t.hidden = true));
  document.getElementById("tab-" + tab).hidden = false;
  document.getElementById("page-title").textContent = TITLES[tab];
  if (tab !== "jobs" && JOBS_POLL) { clearInterval(JOBS_POLL); JOBS_POLL = null; }
  if (tab === "overview") renderOverview();
  if (tab === "posts") renderPosts();
  if (tab === "kb") renderKB();
  if (tab === "people") renderPeople();
  if (tab === "analytics") renderAnalytics();
  if (tab === "settings") renderSettings();
  if (tab === "jobs") renderJobs();
  if (tab === "judging") renderJudging();
}

// ───────── Tab: People (Knowledge Graph) ─────────
// Combines a read-only KG view (crawler rows) with add/edit/remove for
// dashboard-created people (source='dashboard'). Writes require server mode.
let PEOPLE_SEARCH = "";
let PEOPLE_ORG_ID = null;   // null = all orgs
let PEOPLE_EDIT_KEY = null; // null = add mode; set to person key when editing

function savePerson(orgId, form) {
  const body = { org_id: orgId, name: form.name, title: form.title,
                 role_type: form.role_type, email: form.email, about: form.about };
  if (isServerMode()) {
    serverFetch("/people", { method: "POST", body })
      .then((res) => {
        if (res && res.success === false) { toast(res.error || "Server error", false); return; }
        toast("Person saved ✅");
        PEOPLE_EDIT_KEY = null;
        reloadDbQuietly().then(renderPeople);
      })
      .catch((e) => toast("Server error: " + e.message, false));
  } else {
    alert("Editing people requires the dashboard server (run v2/local_server.py).");
  }
}

function removePerson(personKey, orgId) {
  if (!orgId) { toast("Select an org first (use the picker above) to remove a person", false); return; }
  if (!isServerMode()) { alert("Editing people requires the dashboard server (run v2/local_server.py)."); return; }
  if (!confirm("Remove this person/role? (kept in history, can be re-added)")) return;
  serverFetch("/people/remove", { method: "POST", body: { person_key: personKey, org_id: orgId } })
    .then((res) => {
      if (res && res.success === false) { toast(res.error || "Server error", false); return; }
      toast("Person removed ✅");
      reloadDbQuietly().then(renderPeople);
    })
    .catch((e) => toast("Server error: " + e.message, false));
}

function renderPeople() {
  const el = document.getElementById("tab-people");
  const hasGraph = scalar("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='nodes'");
  if (!hasGraph) {
    el.innerHTML = emptyMsg("No knowledge-graph tables yet. Run the explore() gather first.");
    return;
  }
  const total = scalar("SELECT COUNT(*) FROM nodes WHERE type='Person' AND is_active=1") || 0;
  const appts = scalar("SELECT COUNT(*) FROM edges WHERE type='has_role' AND is_active=1") || 0;
  const areas = scalar("SELECT COUNT(*) FROM nodes WHERE type='ResearchArea' AND is_active=1") || 0;
  const multi = scalar("SELECT COUNT(*) FROM (SELECT src_id FROM edges WHERE type='has_role' "
    + "AND is_active=1 GROUP BY src_id HAVING COUNT(*)>1)") || 0;
  const pend = scalar("SELECT COUNT(*) FROM frontier WHERE status='pending'") || 0;

  // Org picker data
  const orgs = query("SELECT id, name FROM organizations WHERE is_active=1 ORDER BY name");

  const like = "%" + PEOPLE_SEARCH + "%";
  // Base query: extended with n.key and n.source so edit/remove and read-only guard work.
  let peopleSql =
    "SELECT n.id, n.key, n.name, n.source, "
    + "(SELECT group_concat(o.name || ' — ' || "
    + "    COALESCE(json_extract(e.attrs,'$.titles[0]'), e.category, '?'), '; ') "
    + "  FROM edges e JOIN nodes o ON o.id=e.dst_id "
    + "  WHERE e.src_id=n.id AND e.type='has_role' AND e.is_active=1) AS roles, "
    + "(SELECT COUNT(*) FROM edges r WHERE r.src_id=n.id AND r.type='researches' AND r.is_active=1) AS areas, "
    + "(SELECT COUNT(*) FROM frontier f WHERE f.from_node_id=n.id AND f.status='pending') AS frontier, "
    + "json_extract(n.attrs,'$.email') AS email, "
    + "(SELECT json_extract(e.attrs,'$.titles[0]') FROM edges e "
    + "  WHERE e.src_id=n.id AND e.type='has_role' AND e.is_active=1 LIMIT 1) AS edit_title, "
    + "(SELECT e.category FROM edges e "
    + "  WHERE e.src_id=n.id AND e.type='has_role' AND e.is_active=1 LIMIT 1) AS edit_category, "
    + "(SELECT json_extract(ki.metadata,'$.about') FROM knowledge_items ki "
    + "  WHERE ki.is_active=1 AND ki.created_by='dashboard' "
    + "  AND json_extract(ki.metadata,'$.entity_id')=n.key LIMIT 1) AS edit_about "
    + "FROM nodes n WHERE n.type='Person' AND n.is_active=1 ";
  const sqlParams = [];
  if (PEOPLE_SEARCH) {
    peopleSql += "AND (n.name LIKE ? OR n.key LIKE ?) ";
    sqlParams.push(like, like);
  }
  peopleSql += "ORDER BY (SELECT COUNT(*) FROM edges e3 WHERE e3.src_id=n.id AND e3.type='has_role' "
    + "AND e3.is_active=1) DESC, n.name";

  let rows = query(peopleSql, sqlParams);

  // Client-side org filter: only show people who have a has_role edge to the selected org.
  if (PEOPLE_ORG_ID !== null) {
    const orgIdNum = Number(PEOPLE_ORG_ID);
    const memberIds = new Set(
      query(
        "SELECT DISTINCT e.src_id FROM edges e JOIN nodes o ON o.id=e.dst_id "
        + "WHERE e.type='has_role' AND e.is_active=1 AND json_extract(o.attrs,'$.org_id')=?",
        [orgIdNum]
      ).map((r) => r.src_id)
    );
    rows = rows.filter((r) => memberIds.has(r.id));
  }

  const errs = query(
    "SELECT f.url, f.error, n.name FROM frontier f LEFT JOIN nodes n ON n.id=f.from_node_id "
    + "WHERE f.status='error' ORDER BY n.name");

  const orgOptions = orgs.map((o) =>
    `<option value="${o.id}"${PEOPLE_ORG_ID == o.id ? " selected" : ""}>${esc(o.name)}</option>`
  ).join("");

  // Pre-fill edit form if in edit mode
  let editRow = null;
  if (PEOPLE_EDIT_KEY) {
    editRow = rows.find((r) => r.key === PEOPLE_EDIT_KEY) || null;
  }

  el.innerHTML = `
    <div class="kb-list-head">
      <div class="kb-list-title"><strong>${total}</strong> people ·
        <strong>${appts}</strong> appointments · <strong>${multi}</strong> multi-role ·
        <strong>${areas}</strong> research areas · <strong>${pend}</strong> frontier next-steps</div>
      <div class="list-toolbar" style="border:0;padding:10px 0 0;gap:8px;display:flex;flex-wrap:wrap;align-items:center">
        <input id="people-search" type="text" placeholder="Search name…" value="${esc(PEOPLE_SEARCH)}">
        <select id="people-org" style="min-width:160px">
          <option value="">All orgs</option>
          ${orgOptions}
        </select>
        <button class="btn btn-ghost btn-sm" id="people-new-org">+ New club/org</button>
      </div>
    </div>
    <table class="data-table" style="width:100%;border-collapse:collapse">
      <thead><tr style="text-align:left">
        <th style="padding:6px 8px">Name</th><th style="padding:6px 8px">Appointment(s) — role</th>
        <th style="padding:6px 8px">Areas</th><th style="padding:6px 8px">Frontier</th>
        <th style="padding:6px 8px"></th>
      </tr></thead>
      <tbody>${rows.map((r) => {
        const editable = r.source === "dashboard";
        const editBtns = editable
          ? `<button class="btn btn-ghost btn-sm people-edit-btn" data-key="${esc(r.key)}" style="margin-right:4px">Edit</button>`
            + `<button class="btn btn-ghost btn-sm people-rm-btn" data-key="${esc(r.key)}" style="color:#ff8a8a">Remove</button>`
          : `<span class="muted" style="font-size:0.82em">crawler</span>`;
        return `<tr style="border-top:1px solid #2a2a2a">
          <td style="padding:6px 8px">${esc(r.name)}</td>
          <td style="padding:6px 8px">${esc(r.roles || "—")}</td>
          <td style="padding:6px 8px">${r.areas}</td>
          <td style="padding:6px 8px">${r.frontier ? "🔗 " + r.frontier : ""}</td>
          <td style="padding:6px 8px;white-space:nowrap">${editBtns}</td>
        </tr>`;
      }).join("") || `<tr><td colspan="5" class="panel-empty">No people.</td></tr>`}</tbody>
    </table>

    <div class="panel" style="margin-top:20px;max-width:520px">
      <h3 style="margin:0 0 12px">${PEOPLE_EDIT_KEY ? "Edit person" : "Add person"}</h3>
      <div class="form-row">
        <label class="form-label">Name <span style="color:#ff8a8a">*</span></label>
        <input type="text" id="pf-name" class="set-input" value="${esc(editRow ? editRow.name : "")}" placeholder="Full name">
      </div>
      <div class="form-row">
        <label class="form-label">Title <span style="color:#ff8a8a">*</span></label>
        <input type="text" id="pf-title" class="set-input" value="${esc(editRow ? (editRow.edit_title || "") : "")}" placeholder="e.g. Assistant Professor">
      </div>
      <div class="form-row">
        <label class="form-label">Role type</label>
        <select id="pf-role-type" class="set-input">
          ${(function() {
            const ROLE_LABELS = { officer: "Officer", deprep: "Dept Rep", staff: "Staff", advisor: "Advisor", admin: "Admin" };
            const editLabel = editRow ? (ROLE_LABELS[editRow.edit_category] || "Officer") : null;
            return ["Officer","Dept Rep","Staff","Advisor","Admin"].map((rt) =>
              `<option${(editRow ? rt === editLabel : rt === "Officer") ? " selected" : ""}>${esc(rt)}</option>`
            ).join("");
          })()}
        </select>
      </div>
      <div class="form-row">
        <label class="form-label">Email (optional)</label>
        <input type="email" id="pf-email" class="set-input" value="${esc(editRow ? (editRow.email || "") : "")}" placeholder="name@njit.edu">
      </div>
      <div class="form-row">
        <label class="form-label">About (optional)</label>
        <textarea id="pf-about" class="set-input" rows="3" placeholder="Short bio or notes">${esc(editRow ? (editRow.edit_about || "") : "")}</textarea>
      </div>
      <div class="form-buttons" style="justify-content:flex-start;gap:8px">
        <button class="btn btn-primary" id="pf-save">Save</button>
        ${PEOPLE_EDIT_KEY ? `<button class="btn btn-ghost" id="pf-cancel">Cancel</button>` : ""}
      </div>
    </div>

    ${errs.length ? `<div style="margin-top:20px">
      <div class="kb-list-title">⚠️ Crawl issues (${errs.length}) <span class="muted">— frontier sites that failed to load (usually source-side: dead link / DNS / SSL)</span></div>
      <table class="data-table" style="width:100%;border-collapse:collapse;margin-top:6px">
        <thead><tr style="text-align:left">
          <th style="padding:6px 8px">Person</th><th style="padding:6px 8px">URL</th>
          <th style="padding:6px 8px">Reason</th></tr></thead>
        <tbody>${errs.map((e) => `<tr style="border-top:1px solid #2a2a2a">
          <td style="padding:6px 8px">${esc(e.name || "—")}</td>
          <td style="padding:6px 8px"><a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.url)}</a></td>
          <td style="padding:6px 8px">${esc(e.error || "?")}</td>
        </tr>`).join("")}</tbody>
      </table></div>` : ""}`;

  // ── wire search ──
  let deb;
  document.getElementById("people-search").oninput = (e) => {
    clearTimeout(deb);
    deb = setTimeout(() => { PEOPLE_SEARCH = e.target.value.trim(); renderPeople(); }, 200);
  };

  // ── wire org picker ──
  document.getElementById("people-org").onchange = (e) => {
    PEOPLE_ORG_ID = e.target.value ? Number(e.target.value) : null;
    PEOPLE_EDIT_KEY = null;
    renderPeople();
  };

  // ── wire new org button ──
  document.getElementById("people-new-org").onclick = () => {
    const name = prompt("New club / org name:");
    if (!name || !name.trim()) return;
    if (!isServerMode()) { alert("Creating orgs requires the dashboard server (run v2/local_server.py)."); return; }
    serverFetch("/orgs", { method: "POST", body: { name: name.trim() } })
      .then((res) => {
        if (res && res.success === false) { toast(res.error || "Server error", false); return; }
        toast("Org created ✅");
        if (res && res.org_id) PEOPLE_ORG_ID = res.org_id;
        renderPeople();
      })
      .catch((e) => toast("Server error: " + e.message, false));
  };

  // ── wire per-row Edit buttons ──
  el.querySelectorAll(".people-edit-btn").forEach((btn) => {
    btn.onclick = () => {
      PEOPLE_EDIT_KEY = btn.dataset.key;
      renderPeople();
      el.querySelector(".panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
    };
  });

  // ── wire per-row Remove buttons ──
  el.querySelectorAll(".people-rm-btn").forEach((btn) => {
    btn.onclick = () => {
      if (!isServerMode()) { alert("Removing people requires the dashboard server (run v2/local_server.py)."); return; }
      removePerson(btn.dataset.key, PEOPLE_ORG_ID);
    };
  });

  // ── wire Save (add / edit) ──
  document.getElementById("pf-save").onclick = () => {
    const name = document.getElementById("pf-name").value.trim();
    if (!name) { toast("Name is required", false); return; }
    const title = document.getElementById("pf-title").value.trim();
    if (!title) { toast("Title is required", false); return; }
    const targetOrg = PEOPLE_ORG_ID;
    if (!targetOrg) { toast("Select an org first (use the org picker above)", false); return; }
    savePerson(targetOrg, {
      name,
      title,
      role_type: document.getElementById("pf-role-type").value,
      email: document.getElementById("pf-email").value.trim(),
      about: document.getElementById("pf-about").value.trim(),
    });
  };

  // ── wire Cancel (edit mode only) ──
  const cancelBtn = document.getElementById("pf-cancel");
  if (cancelBtn) cancelBtn.onclick = () => { PEOPLE_EDIT_KEY = null; renderPeople(); };
}

// ───────── Tab: Jobs (control plane) ─────────
// Talks to the localhost backend's /api/* endpoints. State-changing calls send
// the X-GSA-Dashboard CSRF header. Only available in server mode (a loaded file
// has no backend to run jobs).
let JOBS_POLL = null;
const JOB_DONE = ["done", "failed", "cancelled", "interrupted"];

function jobsApi(path, opts = {}) {
  const post = opts.method === "POST";
  return fetch(SERVER_URL + path, {
    method: opts.method || "GET",
    headers: post ? { "Content-Type": "application/json", "X-GSA-Dashboard": "1" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  }).then((r) => r.json().catch(() => ({})).then((j) => ({ status: r.status, body: j })));
}

function jobBadge(status) {
  const c = { running: "#f0c674", done: "#7ee2a8", failed: "#ff8a8a",
    cancelled: "#bbb", interrupted: "#d08a4a" };
  return `<span class="job-badge" style="color:${c[status] || "#bbb"}">${esc(status)}</span>`;
}

function renderJobs() {
  const el = document.getElementById("tab-jobs");
  if (!isServerMode()) {
    el.innerHTML = emptyMsg("Data-source refreshes run on the live server. Connect via the "
      + "server URL (not a loaded file) to trigger a refresh.");
    return;
  }
  el.innerHTML = `
    <div class="panel">
      <div id="jobs-health" class="jobs-health">Checking server…</div>
      <div class="jobs-actions">
        <label class="muted" style="margin-right:6px">Refresh:</label>
        <select id="refresh-what" class="btn btn-ghost" style="padding:6px 10px">
          <option value="explore">NJIT people &amp; colleges (faculty, roles, research)</option>
          <option value="office">A specific NJIT office</option>
          <option value="roster">A manual roster</option>
          <option value="frontier">Personal / lab websites</option>
        </select>
        <select id="refresh-target" class="btn btn-ghost" style="padding:6px 10px;display:none"></select>
        <button class="btn btn-primary" id="refresh-run">Run refresh</button>
      </div>
      <div id="jobs-kg-scope" class="jobs-scope"></div>
      <div id="job-active"></div>
      <h3 style="margin-top:18px">Recent runs</h3>
      <div id="jobs-recent">Loading…</div>
      <h3 style="margin-top:18px">Restore from backup</h3>
      <div class="muted" style="margin-bottom:6px">Every refresh snapshots the database first.
        Restoring replaces <strong>all</strong> current data with a snapshot — your current state
        is saved first, so it's reversible.</div>
      <div id="jobs-backups">Loading…</div>
    </div>`;

  const OFFICE_OPTS = [
    ["all", "All offices"], ["registrar", "Registrar"], ["financialaid", "Financial Aid"],
    ["graduatestudies", "Graduate Studies"], ["counseling", "Counseling (C-CAPS)"],
    ["careerservices", "Career Development"], ["dos", "Dean of Students"],
    ["global", "Global Initiatives (OGI)"], ["bursar", "Bursar"]];
  const ROSTER_OPTS = [
    ["theatre", "Theatre Arts & Technology"], ["senior-administration", "NJIT Senior Administration"]];

  const what = document.getElementById("refresh-what");
  const target = document.getElementById("refresh-target");
  const fillTarget = () => {
    const opts = what.value === "office" ? OFFICE_OPTS
      : what.value === "roster" ? ROSTER_OPTS : null;
    if (!opts) { target.style.display = "none"; target.innerHTML = ""; return; }
    target.innerHTML = opts.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("");
    target.style.display = "";
  };
  what.addEventListener("change", fillTarget);
  fillTarget();

  document.getElementById("refresh-run").addEventListener("click", () => {
    const label = (sel) => sel.options[sel.selectedIndex].text;
    let msg;
    if (what.value === "explore")
      msg = "Re-crawl ALL NJIT colleges & departments from njit.edu?\n\nA backup is taken first, "
          + "then new bios/publications are embedded. This can take ~20–30 minutes.";
    else if (what.value === "frontier")
      msg = "Process pending personal / lab websites into the knowledge base?";
    else if (what.value === "office")
      msg = `Re-crawl the "${label(target)}" office pages from njit.edu?\n\nA backup is taken first, `
          + "then the content is ingested and embedded (~a few minutes).";
    else if (what.value === "roster")
      msg = `Re-seed the "${label(target)}" roster from the curated list?\n\nA backup is taken first. `
          + "This restores the curated people/titles (overwrites any drift).";
    if (!window.confirm(msg)) return;
    if (what.value === "explore") startExplore({});
    else if (what.value === "frontier") startExplore({ frontier: true });
    else if (what.value === "office") startCrawlSection(target.value);
    else if (what.value === "roster") startSeedRoster(target.value);
  });
  refreshJobsHealth();
  refreshJobsList();
  refreshBackups();
}

function refreshBackups() {
  jobsApi("/api/backups").then(({ body }) => {
    const el = document.getElementById("jobs-backups");
    if (!el) return;
    const b = (body && body.backups) || [];
    if (!b.length) { el.innerHTML = '<span class="muted">No backups yet.</span>'; return; }
    el.innerHTML = '<table class="data-table" style="width:100%;border-collapse:collapse">'
      + b.slice(0, 15).map((x) =>
        `<tr><td>${esc(x.mtime)}</td><td>${esc(x.label || "—")}</td>`
        + `<td class="muted">${x.size_mb} MB</td>`
        + `<td><button class="btn btn-ghost btn-sm restore-btn" data-file="${esc(x.file)}">Restore</button></td></tr>`
      ).join("") + "</table>";
    el.querySelectorAll(".restore-btn").forEach((btn) =>
      btn.addEventListener("click", () => restoreBackup(btn.dataset.file)));
  }).catch(() => {});
}

function restoreBackup(file) {
  if (!window.confirm(`Restore this backup?\n\n${file}\n\nThis REPLACES ALL current data with that `
      + "snapshot. Your current state is backed up first (reversible), and it's blocked while a "
      + "refresh is running. Best done when the bots are idle. Continue?")) return;
  jobsApi("/api/backups/restore", { method: "POST", body: { file } })
    .then(({ status, body }) => {
      if (status !== 200) { toast((body && body.error) || "Restore failed", false); return; }
      toast("Restored ✅ — current state saved to " + (body.current_saved_to || "a backup"));
      reloadDbQuietly().then(() => renderJobs());
    })
    .catch((e) => toast("Server error: " + e.message, false));
}

function startCrawlSection(section) {
  jobsApi("/api/jobs/crawl-section", { method: "POST", body: { section } })
    .then(({ status, body }) => {
      if (status === 409) { toast("A job is already running", false); return; }
      if (status !== 201) { toast((body && body.error) || "Could not start job", false); return; }
      toast(`Refreshing ${section} from njit.edu ✅`);
      pollJob(body.job_id);
      refreshJobsList();
      refreshJobsHealth();
    })
    .catch((e) => toast("Server error: " + e.message, false));
}

function startSeedRoster(roster) {
  jobsApi("/api/jobs/seed-roster", { method: "POST", body: { roster } })
    .then(({ status, body }) => {
      if (status === 409) { toast("A job is already running", false); return; }
      if (status !== 201) { toast((body && body.error) || "Could not start job", false); return; }
      toast(`Re-seeding ${roster} roster ✅`);
      pollJob(body.job_id);
      refreshJobsList();
      refreshJobsHealth();
    })
    .catch((e) => toast("Server error: " + e.message, false));
}

function startExplore(opts) {
  jobsApi("/api/jobs/explore", { method: "POST", body: opts })
    .then(({ status, body }) => {
      if (status === 409) { toast("A job is already running", false); return; }
      if (status !== 201) { toast((body && body.error) || "Could not start job", false); return; }
      toast(opts.frontier ? "Frontier processing started ✅" : "KG gather started ✅");
      pollJob(body.job_id);
      refreshJobsList();
      refreshJobsHealth();
    })
    .catch((e) => toast("Server error: " + e.message, false));
}

// Render minutes from seconds, rounded to a friendly value.
function etaText(seconds) {
  const m = Math.round(seconds / 60);
  if (m < 1) return "under a minute";
  return `~${m} min`;
}

function refreshJobsHealth() {
  jobsApi("/api/health").then(({ body }) => {
    const h = document.getElementById("jobs-health");
    if (!h) return;
    const ok = !!body.ollama;
    h.innerHTML = "Ollama: " + (ok
      ? '<span style="color:#7ee2a8">● up</span>'
      : '<span style="color:#ff8a8a">● down — overviews & embeddings will fail</span>');

    // Compact coverage hint for the "NJIT people & colleges" refresh — colleges only (the full
    // department list was a wall of text); the acronym in parentheses, or the college name.
    const kgScope = document.getElementById("jobs-kg-scope");
    if (kgScope && body.crawl_scope) {
      const acronym = (name) => {
        const m = name.match(/\(([^)]+)\)/);
        return m ? m[1] : name;
      };
      const n = body.crawl_scope.reduce((s, c) => s + (c.areas || []).length, 0);
      kgScope.innerHTML = '<span class="muted">🕸 Covers '
        + body.crawl_scope.map((c) => esc(acronym(c.college))).join(", ")
        + ` — ${n} departments/units.</span>`;
    }

    // Disable Run while a job is in flight; keep polling it.
    const runBtn = document.getElementById("refresh-run");
    if (runBtn) runBtn.disabled = !ok || !!body.running_job;
    if (body.running_job) pollJob(body.running_job.id);
  }).catch(() => {});
}

// Reload the in-browser DB copy from the server WITHOUT re-rendering (so the
// Jobs view isn't disrupted) — used when a job finishes so KB/Overview reflect
// the freshly-ingested data on next view.
function reloadDbQuietly() {
  if (!isServerMode()) return Promise.resolve();
  return fetch(SERVER_URL + "/db").then((r) => r.arrayBuffer()).then((buf) => {
    db = new SQL.Database(new Uint8Array(buf));
    PL.prepareForDashboard(db);
  }).catch(() => {});
}

function pollJob(id) {
  if (JOBS_POLL) clearInterval(JOBS_POLL);
  const tick = () => jobsApi("/api/jobs/" + id).then(({ body }) => {
    renderActiveJob(body);
    if (body && JOB_DONE.includes(body.status)) {
      clearInterval(JOBS_POLL); JOBS_POLL = null;
      // job wrote to the DB — pull a fresh snapshot so other tabs are current
      reloadDbQuietly().then(() => { refreshJobsHealth(); refreshJobsList(); });
      return;
    }
  }).catch(() => {});
  tick();
  JOBS_POLL = setInterval(tick, 3000);
}

function renderActiveJob(job) {
  const el = document.getElementById("job-active");
  if (!el || !job || !job.id) return;
  const cancel = job.status === "running"
    ? '<button class="btn" id="job-cancel">Cancel</button>' : "";
  el.innerHTML = `
    <div class="job-card">
      <div class="job-head">Job #${job.id} · ${esc(job.type)} ${jobBadge(job.status)} ${cancel}</div>
      <pre class="job-log">${esc(job.log_tail || "")}</pre>
      ${job.summary ? `<div class="job-summary">${esc(job.summary)}</div>` : ""}
    </div>`;
  const cb = document.getElementById("job-cancel");
  if (cb) cb.addEventListener("click", () =>
    jobsApi("/api/jobs/" + job.id + "/cancel", { method: "POST" })
      .then(() => toast("Cancelling…")).catch(() => {}));
  const log = el.querySelector(".job-log");
  if (log) log.scrollTop = log.scrollHeight;
}

function refreshJobsList() {
  jobsApi("/api/jobs").then(({ body }) => {
    const el = document.getElementById("jobs-recent");
    if (!el) return;
    const jobs = (body && body.jobs) || [];
    if (!jobs.length) { el.innerHTML = emptyMsg("No jobs yet."); return; }
    el.innerHTML = jobs.map((j) => `
      <div class="job-row" data-id="${j.id}">
        <span class="job-id">#${j.id} ${esc(j.type)}</span>
        ${jobBadge(j.status)}
        <span class="job-when">${relTime(j.started_at)}</span>
        <span class="job-sum">${esc(preview(j.summary || "", 60))}</span>
      </div>`).join("");
    el.querySelectorAll(".job-row").forEach((r) =>
      r.addEventListener("click", () => pollJob(Number(r.dataset.id))));
  }).catch(() => {});
}

// ───────── Tab 1: Overview ─────────
function renderOverview() {
  const totalQ = scalar("SELECT COUNT(*) FROM questions") ?? 0;
  const answered = scalar("SELECT SUM(CASE WHEN confidence>=50 THEN 1 ELSE 0 END) FROM questions") ?? 0;
  const answerRate = totalQ ? Math.round((answered / totalQ) * 1000) / 10 : 0;
  const kiActive = scalar("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1") ?? 0;
  const kiInactive = scalar("SELECT COUNT(*) FROM knowledge_items WHERE is_active=0") ?? 0;
  const activeEvents = scalar("SELECT COUNT(*) FROM events WHERE date >= date('now')") ?? 0;
  const orgCount = scalar("SELECT COUNT(*) FROM organizations") ?? 0;
  // embeddings via vec0 shadow table (sql.js can't query the vec0 table itself)
  const embeds = scalar("SELECT COUNT(*) FROM knowledge_vectors_rowids") ?? 0;
  const qToday = scalar("SELECT COUNT(*) FROM questions WHERE DATE(timestamp)=DATE('now')") ?? 0;
  const lastSent = scalar("SELECT MAX(sent_at) FROM posts WHERE sent_at IS NOT NULL");
  const nextSched = scalar("SELECT MIN(scheduled_for) FROM posts WHERE status='scheduled' AND scheduled_for IS NOT NULL");

  const recent = query(
    "SELECT id,type,title,content,status,sent_at,created_at FROM posts ORDER BY created_at DESC LIMIT 10");
  const upcoming = query(
    "SELECT id,type,title,content,channels,scheduled_for FROM posts " +
    "WHERE status='scheduled' ORDER BY scheduled_for ASC LIMIT 10");

  document.getElementById("tab-overview").innerHTML = `
    <div class="stat-grid">
      ${statCard("Total Questions", totalQ, "all-time, both platforms", true)}
      ${statCard("Answer Rate", `${answerRate}<span class="unit">%</span>`, "confidence ≥ 50")}
      ${statCard("Knowledge Items", kiActive, `${embeds}/${kiActive} embedded`)}
      ${statCard("Active Events", activeEvents, "upcoming")}
    </div>

    <div class="panel-row">
      <div class="panel">
        <h3>Recent Posts</h3>
        <div class="panel-body">${recent.length ? recent.map(recentRow).join("") : emptyMsg("No posts sent yet")}</div>
      </div>
      <div class="panel">
        <h3>Upcoming Scheduled</h3>
        <div class="panel-body">${upcoming.length ? upcoming.map(upcomingRow).join("") : emptyMsg("No scheduled posts")}</div>
      </div>
    </div>

    <div class="health">
      <h3>System Health</h3>
      <div class="health-grid">
        ${healthItem("Knowledge items", `${kiActive} <span style="color:#6b7280;font-weight:500">(${kiActive} active, ${kiInactive} inactive)</span>`)}
        ${healthItem("Organizations", `${orgCount} nodes`)}
        ${healthItem("Embeddings", `<span class="dot ${embeds >= kiActive && kiActive > 0 ? "ok" : "warn"}"></span>${embeds}/${kiActive} covered`)}
        ${healthItem("Last post sent", lastSent ? relTime(lastSent) : "—")}
        ${healthItem("Next scheduled", nextSched ? absTime(nextSched) : "—")}
        ${healthItem("V1 bot questions today", qToday)}
      </div>
    </div>`;

  // wire row clicks
  document.querySelectorAll("#tab-overview .row-item").forEach((el) => {
    el.addEventListener("click", () => openPost(Number(el.dataset.id)));
  });
}

function statCard(label, value, sub, accent = false) {
  return `<div class="stat-card${accent ? " accent" : ""}">
    <div class="label">${label}</div>
    <div class="value">${value}</div>
    <div class="sub">${sub}</div>
  </div>`;
}
function healthItem(label, value) {
  return `<div class="health-item"><div class="h-label">${label}</div><div class="h-value">${value}</div></div>`;
}
function emptyMsg(t) { return `<div class="panel-empty">${t}</div>`; }

function recentRow(p) {
  const title = esc(p.title || preview(p.content));
  return `<div class="row-item" data-id="${p.id}">
    <div class="row-icon">${iconFor(p.type)}</div>
    <div class="row-main">
      <div class="row-title">${title}</div>
      <div class="row-meta">${esc(p.type)} · ${p.sent_at ? relTime(p.sent_at) : "not sent"}</div>
    </div>
    <div class="row-right"><span class="badge ${esc(p.status)}">${esc(p.status)}</span></div>
  </div>`;
}
function upcomingRow(p) {
  let plats = [];
  try { plats = JSON.parse(p.channels || "[]"); } catch (e) {}
  const badges = plats.map((x) => `<span class="plat">${esc(x)}</span>`).join(" ");
  return `<div class="row-item" data-id="${p.id}">
    <div class="row-icon">${iconFor(p.type)}</div>
    <div class="row-main">
      <div class="row-title">${esc(p.title || preview(p.content))}</div>
      <div class="row-meta">${absTime(p.scheduled_for)}</div>
    </div>
    <div class="row-right">${badges}</div>
  </div>`;
}

// ───────── post detail modal ─────────
function openPost(id) {
  const p = one("SELECT * FROM posts WHERE id=?", [id]);
  if (!p) return;
  const deliveries = query(
    "SELECT platform,channel,status,error,sent_at FROM post_deliveries WHERE post_id=? ORDER BY platform", [id]);
  let plats = []; try { plats = JSON.parse(p.channels || "[]"); } catch (e) {}

  const delHtml = deliveries.length
    ? deliveries.map((d) => `<div class="row-meta">${esc(d.platform)} → ${esc(d.channel || "—")} · <span class="badge ${esc(d.status)}">${esc(d.status)}</span>${d.error ? " · " + esc(d.error) : ""}</div>`).join("")
    : '<div class="row-meta">No delivery records.</div>';

  document.getElementById("modal-body").innerHTML = `
    <h2>${iconFor(p.type)} ${esc(p.title || preview(p.content, 60))}</h2>
    <span class="badge ${esc(p.status)}">${esc(p.status)}</span>
    <dl class="kv">
      <dt>Type</dt><dd>${esc(p.type)}</dd>
      <dt>Platforms</dt><dd>${plats.map((x) => `<span class="plat">${esc(x)}</span>`).join(" ") || "—"}</dd>
      <dt>Discord channel</dt><dd>${esc(p.discord_channel || "—")}</dd>
      <dt>Scheduled for</dt><dd>${p.scheduled_for ? absTime(p.scheduled_for) : "—"}</dd>
      <dt>Sent at</dt><dd>${p.sent_at ? absTime(p.sent_at) : "—"}</dd>
      <dt>Created</dt><dd>${absTime(p.created_at)}</dd>
    </dl>
    <div class="content-box">${esc(p.content)}</div>
    <h3 style="margin:18px 0 6px;font-size:13px">Deliveries</h3>
    ${delHtml}
    <div class="modal-actions">
      ${p.status === "scheduled" ? `<button class="btn btn-danger" id="cancel-post">Cancel post</button>` : ""}
      <button class="btn btn-ghost" id="close-2">Close</button>
    </div>`;
  document.getElementById("modal").hidden = false;

  const closeBtn = document.getElementById("close-2");
  if (closeBtn) closeBtn.onclick = closeModal;
  const cancel = document.getElementById("cancel-post");
  if (cancel) cancel.onclick = () => {
    db.run("UPDATE posts SET status='cancelled' WHERE id=?", [id]);
    closeModal();
    renderOverview();
    // Note: changes live in the in-memory db. Persisting back to the .db file
    // (download/export) is wired up in a later checkpoint.
  };
}
function closeModal() { document.getElementById("modal").hidden = true; }
document.getElementById("modal-close").addEventListener("click", closeModal);
document.getElementById("modal").addEventListener("click", (e) => {
  if (e.target.id === "modal") closeModal();
});

// ═════════════════════════ Tab 2: Posts ═════════════════════════
const val = (id) => { const el = document.getElementById(id); return el ? el.value : ""; };

const PS = { filter: "all", search: "", page: 1, pageSize: 20, selectedId: null, view: "none" };
let FormState = null;

function renderPosts() {
  document.getElementById("tab-posts").innerHTML = `
    <div class="posts-wrap">
      <div class="posts-col">
        <div class="list-toolbar">
          <button class="btn btn-primary btn-sm" id="new-post-btn">+ New Post</button>
          <select id="post-filter">
            <option value="all">All posts</option>
            <option value="scheduled">Scheduled</option>
            <option value="sent">Sent</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </select>
          <input id="post-search" type="text" placeholder="Search…" value="${esc(PS.search)}" />
        </div>
        <div class="plist" id="plist"></div>
        <div class="pager" id="pager"></div>
      </div>
      <div class="posts-col detail-pane" id="detail-pane"></div>
    </div>`;
  document.getElementById("post-filter").value = PS.filter;
  document.getElementById("new-post-btn").onclick = openNewPostForm;
  document.getElementById("post-filter").onchange = (e) => { PS.filter = e.target.value; PS.page = 1; renderPostsList(); };
  let deb;
  document.getElementById("post-search").oninput = (e) => {
    clearTimeout(deb); deb = setTimeout(() => { PS.search = e.target.value.trim(); PS.page = 1; renderPostsList(); }, 200);
  };
  renderPostsList();
  renderRightPane();
}

function listWhere() {
  const w = ["1=1"], p = [];
  if (PS.filter !== "all") { w.push("status=?"); p.push(PS.filter); }
  if (PS.search) { w.push("(content LIKE ? OR title LIKE ?)"); p.push("%" + PS.search + "%", "%" + PS.search + "%"); }
  return { clause: w.join(" AND "), params: p };
}

function renderPostsList() {
  const { clause, params } = listWhere();
  const total = scalar(`SELECT COUNT(*) FROM posts WHERE ${clause}`, params) || 0;
  const pages = Math.max(1, Math.ceil(total / PS.pageSize));
  if (PS.page > pages) PS.page = pages;
  const offset = (PS.page - 1) * PS.pageSize;
  const rows = query(
    `SELECT id,type,title,content,status,channels,scheduled_for,sent_at FROM posts WHERE ${clause} ` +
    `ORDER BY COALESCE(scheduled_for,created_at) DESC, id DESC LIMIT ? OFFSET ?`, [...params, PS.pageSize, offset]);
  const list = document.getElementById("plist");
  list.innerHTML = rows.length ? rows.map(plistItem).join("") : `<div class="panel-empty">No posts match.</div>`;
  list.querySelectorAll(".plist-item").forEach((el) =>
    el.addEventListener("click", () => { PS.selectedId = Number(el.dataset.id); PS.view = "detail"; renderPostsList(); renderRightPane(); }));
  document.getElementById("pager").innerHTML =
    `<button id="pg-prev" ${PS.page <= 1 ? "disabled" : ""}>← Prev</button>` +
    ` Page ${PS.page} of ${pages} (${total}) ` +
    `<button id="pg-next" ${PS.page >= pages ? "disabled" : ""}>Next →</button>`;
  const prev = document.getElementById("pg-prev"), next = document.getElementById("pg-next");
  if (prev) prev.onclick = () => { PS.page--; renderPostsList(); };
  if (next) next.onclick = () => { PS.page++; renderPostsList(); };
}

function platBadges(channels) {
  let plats = []; try { plats = JSON.parse(channels || "[]"); } catch (e) {}
  return plats.map((x) => `<span class="plat plat-${esc(x[0])}">${esc(x[0].toUpperCase())}</span>`).join(" ");
}

function plistItem(p) {
  const t = p.scheduled_for || p.sent_at;
  return `<div class="plist-item${p.id === PS.selectedId ? " selected" : ""}" data-id="${p.id}">
    <div class="row-icon">${iconFor(p.type)}</div>
    <div class="row-main">
      <div class="row-title">${esc(p.title || preview(p.content, 44))}</div>
      <div class="plist-meta"><span class="badge ${esc(p.status)}">${esc(p.status)}</span>
        <span class="row-meta">${t ? relTime(t) : ""}</span></div>
    </div>
    <div class="row-right">${platBadges(p.channels)}</div>
  </div>`;
}

function renderRightPane() {
  if (PS.view === "form") renderNewPostForm();
  else if (PS.view === "detail" && PS.selectedId) renderPostDetail(PS.selectedId);
  else document.getElementById("detail-pane").innerHTML =
    `<div class="detail-placeholder">Select a post to view details<br/>or click <strong>+ New Post</strong> to create one.</div>`;
}

// ── detail view ──────────────────────────────────────────────────────────
function renderPostDetail(id) {
  const p = one("SELECT * FROM posts WHERE id=?", [id]);
  if (!p) { PS.view = "none"; renderRightPane(); return; }
  const deliveries = query("SELECT platform,channel,status,error,sent_at FROM post_deliveries WHERE post_id=? ORDER BY platform", [id]);
  const sig = PL.renderSignature(db, p.org_id, p.signature);
  const orgName = scalar("SELECT name FROM organizations WHERE id=?", [p.org_id]) || "—";
  const delRows = deliveries.length
    ? deliveries.map((d) => `<tr><td>${esc(d.platform)}</td><td>${esc(d.channel || "—")}</td>
        <td><span class="badge ${esc(d.status)}">${esc(d.status)}</span></td>
        <td>${d.sent_at ? absTime(d.sent_at) : "—"}</td><td>${esc(d.error || "")}</td></tr>`).join("")
    : `<tr><td colspan="5" class="muted">Not yet sent</td></tr>`;
  const actions = [];
  if (p.status === "scheduled") actions.push(`<button class="btn btn-danger btn-sm" id="d-cancel">Cancel Post</button>`);
  if (p.status === "sent" || p.status === "failed") actions.push(`<button class="btn btn-ghost btn-sm" id="d-resend">Resend</button>`);

  document.getElementById("detail-pane").innerHTML = `
    <div class="detail-head">
      <div class="row-icon" style="font-size:24px">${iconFor(p.type)}</div>
      <h2>${esc(p.title || preview(p.content, 60))}</h2>
      <div class="actions">${actions.join("")}</div>
    </div>
    <span class="badge ${esc(p.status)}">${esc(p.status)}</span>
    <div class="section-label">Content</div>
    <div class="content-box">${esc(p.content)}</div>
    ${sig ? `<div class="section-label">Signature</div><div class="sig-box">${esc(sig)}</div>` : ""}
    <div class="section-label">Delivered to</div>
    <table class="deliv-table"><thead><tr><th>Platform</th><th>Channel</th><th>Status</th><th>Time</th><th>Error</th></tr></thead>
      <tbody>${delRows}</tbody></table>
    <div class="section-label">Details</div>
    <dl class="kv">
      <dt>Platforms</dt><dd>${platBadges(p.channels) || "—"}</dd>
      <dt>Scheduled for</dt><dd>${p.scheduled_for ? absTime(p.scheduled_for) : "—"}</dd>
      <dt>Sent at</dt><dd>${p.sent_at ? absTime(p.sent_at) : "—"}</dd>
      <dt>Created by</dt><dd>${esc(p.created_by || "—")}</dd>
      <dt>Source</dt><dd>${esc(p.source_type || "—")}${p.source_id ? ` (#${p.source_id})` : ""}</dd>
      <dt>Organization</dt><dd>${esc(orgName)}</dd>
    </dl>`;
  const c = document.getElementById("d-cancel");
  if (c) c.onclick = () => { applyAndExport(`UPDATE posts SET status='cancelled' WHERE id=${id};`, "Cancel post", { type: "post", server: { path: `/posts/${id}`, method: "DELETE" } }); if (!isServerMode()) { renderPostsList(); renderPostDetail(id); } };
  const r = document.getElementById("d-resend");
  if (r) r.onclick = () => { applyAndExport(`UPDATE posts SET status='scheduled', sent_at=NULL WHERE id=${id};`, "Re-queue post", { type: "post" }); if (!isServerMode()) { renderPostsList(); renderPostDetail(id); } };
}

// ── new post form ────────────────────────────────────────────────────────
function openNewPostForm(mode) {
  if (!db) return;
  if (document.getElementById("tab-posts").hidden) switchTab("posts");
  FormState = newFormState();
  if (mode && ["one_time", "recurring", "event"].includes(mode)) FormState.type = mode;
  PS.view = "form"; PS.selectedId = null;
  renderPostsList();
  renderNewPostForm();
}

function newFormState() {
  const gsaId = scalar("SELECT id FROM organizations WHERE slug='gsa'") || scalar("SELECT id FROM organizations ORDER BY id LIMIT 1");
  const platforms = PL.getSettingJSON(db, gsaId, "default.platforms", ["discord"]) || ["discord"];
  const sendTime = PL.getSetting(db, gsaId, "default.send_time", "09:00") || "09:00";
  const reminders = (PL.getSettingJSON(db, gsaId, "reminders.default", []) || [])
    .map((r) => ({ enabled: true, offset: r.offset, unit: r.unit, platforms: (r.channels || platforms).slice() }));
  return { type: "one_time", orgId: gsaId, platforms: platforms.slice(), sendTime, days: [], sigOverride: null, previewPlat: "discord", reminders };
}

function orgOptions(selectedId) {
  const orgs = query(
    "WITH RECURSIVE t(id,name,parent_id,depth) AS (" +
    " SELECT id,name,parent_id,0 FROM organizations WHERE parent_id IS NULL" +
    " UNION ALL SELECT o.id,o.name,o.parent_id,t.depth+1 FROM organizations o JOIN t ON o.parent_id=t.id" +
    ") SELECT id,name,depth FROM t ORDER BY depth, name");
  return orgs.map((o) => `<option value="${o.id}" ${o.id === selectedId ? "selected" : ""}>${"   ".repeat(o.depth)}${esc(o.name)}</option>`).join("");
}

function channelOptions() {
  const chans = query("SELECT DISTINCT value v FROM settings WHERE key LIKE 'default.channel.%' AND value IS NOT NULL ORDER BY value");
  return chans.map((c) => `<option value="${esc(c.v)}">${esc(c.v)}</option>`).join("");
}

const tomorrow9 = () => { const d = new Date(Date.now() + 864e5); return PL.fmtDate(d); };

function renderNewPostForm() {
  const fs = FormState;
  const tg = PL.getSetting(db, fs.orgId, "org.telegram_channel", "") || "—";
  const gm = PL.getSetting(db, fs.orgId, "org.groupme_group", "") || "GSAGateWayNJIT";
  document.getElementById("detail-pane").innerHTML = `
   <div class="form">
    <h2>New Post</h2>

    <div class="fsection">
      <div class="section-label">1 · What</div>
      <div class="field">
        <label>Content</label>
        <textarea id="f-content" rows="6" placeholder="Write your message…"></textarea>
        <div class="charcount"><span id="f-charcount">0</span> characters</div>
      </div>
      <div class="field"><label>Belongs to</label><select id="f-org">${orgOptions(fs.orgId)}</select></div>
    </div>

    <div class="fsection">
      <div class="section-label">2 · When</div>
      <div class="segmented" id="mode-seg">
        <button data-m="one_time" class="on">One-time</button>
        <button data-m="recurring">Recurring</button>
        <button data-m="event">Event</button>
      </div>

      <div id="grp-onetime" class="mode-grp" style="margin-top:14px">
        <div class="inline">
          <div class="field"><label>Send date (${esc(PL.orgTimezone(db))})</label><input type="date" id="f-date" value="${tomorrow9()}"></div>
          <div class="field"><label>Send time</label><input type="time" id="f-time" value="${esc(fs.sendTime)}"></div>
        </div>
        <div class="muted utc-hint" id="onetime-utc"></div>
        <label class="checkrow"><input type="checkbox" id="f-now"> Send immediately</label>
      </div>

      <div id="grp-recurring" class="mode-grp" hidden style="margin-top:14px">
        <div class="inline">
          <div class="field"><label>Repeat every</label><input type="number" id="f-interval" min="1" value="1" style="width:70px"></div>
          <div class="field"><label>&nbsp;</label>
            <select id="f-unit"><option value="day">day(s)</option><option value="week">week(s)</option><option value="month">month(s)</option></select></div>
          <div class="field"><label>At time</label><input type="time" id="f-rtime" value="${esc(fs.sendTime)}"></div>
        </div>
        <div class="field" id="grp-days" hidden>
          <label>Days of week</label>
          <div class="daytoggles" id="day-toggles">
            ${["M", "T", "W", "T", "F", "S", "S"].map((d, i) => `<button data-d="${i}">${d}</button>`).join("")}
          </div>
        </div>
        <div class="inline">
          <div class="field"><label>Start date</label><input type="date" id="f-start" value="${PL.fmtDate(new Date())}"></div>
          <div class="field"><label>End date</label><input type="date" id="f-end"></div>
        </div>
        <label class="checkrow"><input type="checkbox" id="f-noend" checked> No end date</label>
        <div class="section-label">Preview</div>
        <div class="occ-preview" id="occ-preview"></div>
      </div>

      <div id="grp-event" class="mode-grp" hidden style="margin-top:14px">
        <div class="field"><label>Event name</label><input type="text" id="f-evname" placeholder="e.g. Grad Mixer"></div>
        <div class="inline">
          <div class="field"><label>Event date</label><input type="date" id="f-evdate"></div>
          <div class="field"><label>Event time</label><input type="time" id="f-evtime" value="18:00"></div>
        </div>
        <div class="field"><label>Location</label><input type="text" id="f-evloc" placeholder="e.g. Campus Center Ballroom"></div>
        <div class="field">
          <label>Announce</label>
          <label class="checkrow"><input type="radio" name="ann" id="f-ann-now" checked> Send immediately</label>
          <label class="checkrow"><input type="radio" name="ann" id="f-ann-sched"> Schedule:
            <input type="date" id="f-ann-date"> <input type="time" id="f-ann-time" value="09:00"></label>
        </div>
        <div class="section-label">Automatic Reminders <span class="muted">— defaults from your settings</span></div>
        <div id="rem-list"></div>
      </div>
    </div>

    <div class="fsection">
      <div class="section-label">3 · Where</div>
      <div class="inline" style="margin-bottom:10px">
        <label class="checkrow"><input type="checkbox" id="f-pf-discord" ${fs.platforms.includes("discord") ? "checked" : ""}> Discord</label>
        <label class="checkrow"><input type="checkbox" id="f-pf-telegram" ${fs.platforms.includes("telegram") ? "checked" : ""}> Telegram</label>
        <label class="checkrow"><input type="checkbox" id="f-pf-groupme" ${fs.platforms.includes("groupme") ? "checked" : ""}> GroupMe</label>
      </div>
      <div class="inline">
        <div class="field" style="flex:1"><label>Discord channel</label><select id="f-dchan">${channelOptions()}</select></div>
        <div class="field"><label>Telegram channel</label><input type="text" value="${esc(tg)}" readonly></div>
        <div class="field"><label>GroupMe group</label><input type="text" value="${esc(gm)}" readonly></div>
      </div>
    </div>

    <div class="fsection">
      <div class="section-label">4 · Signature</div>
      <div class="sig-box" id="sig-display"></div>
      <div style="margin-top:6px"><a href="#" id="sig-edit-link" class="muted">Edit signature for this post</a></div>
      <div id="sig-edit-wrap" hidden style="margin-top:8px">
        <textarea id="sig-edit" rows="2"></textarea>
        <a href="#" id="sig-reset" class="muted">Reset to default</a>
      </div>
    </div>

    <div class="fsection">
      <div class="section-label">5 · Preview</div>
      <div class="preview-toggle" id="pv-toggle">
        <button data-p="discord" class="on">Discord</button>
        <button data-p="telegram">Telegram</button>
        <button data-p="groupme">GroupMe</button>
      </div>
      <div id="pv-discord" class="preview-discord"></div>
      <div id="pv-telegram" class="preview-telegram" hidden></div>
      <div id="pv-groupme" class="preview-groupme" hidden></div>
    </div>

    <div id="grp-addkb" class="checkrow" style="margin-top:12px">
      <label><input type="checkbox" id="f-addkb"> 📚 Also add to Knowledge Base
        <span class="muted">— lets the bot answer questions about this (run rebuild_index.py after)</span></label>
    </div>

    <div class="form-buttons">
      <button class="btn btn-ghost" id="f-cancel">Cancel</button>
      <button class="btn btn-primary" id="f-submit">Schedule Post</button>
    </div>
   </div>`;

  wireForm();
  setMode(FormState.type);
  renderReminders();
  updateSignatureBox();
  updatePreview();
}

function wireForm() {
  document.getElementById("f-content").addEventListener("input", () => {
    document.getElementById("f-charcount").textContent = String(val("f-content").length);
    updatePreview();
  });
  document.getElementById("f-org").addEventListener("change", (e) => {
    FormState.orgId = Number(e.target.value); updateSignatureBox(); updatePreview();
  });
  const updateUtcHint = () => {
    const el = document.getElementById("onetime-utc"); if (!el) return;
    if (document.getElementById("f-now").checked) { el.textContent = "= now (UTC)"; return; }
    const d = val("f-date"), t = val("f-time");
    el.textContent = (d && t) ? `= ${PL.localToUTC(`${d} ${t}`, PL.orgTimezone(db))} UTC` : "";
  };
  ["f-date", "f-time", "f-now"].forEach((id) => document.getElementById(id).addEventListener("input", updateUtcHint));
  document.getElementById("f-now").addEventListener("change", updateUtcHint);
  updateUtcHint();
  document.querySelectorAll("#mode-seg button").forEach((b) =>
    b.addEventListener("click", () => setMode(b.dataset.m)));
  // recurring inputs
  ["f-interval", "f-unit", "f-rtime", "f-start", "f-end", "f-noend"].forEach((id) =>
    document.getElementById(id).addEventListener("input", () => {
      document.getElementById("grp-days").hidden = (val("f-unit") !== "week");
      updateOccPreview();
    }));
  document.querySelectorAll("#day-toggles button").forEach((b, jsIdx) =>
    b.addEventListener("click", () => {
      // store as Python weekday (Mon=0); toggle buttons are already Mon..Sun
      const d = Number(b.dataset.d);
      const i = FormState.days.indexOf(d);
      if (i >= 0) FormState.days.splice(i, 1); else FormState.days.push(d);
      b.classList.toggle("on"); updateOccPreview();
    }));
  // event inputs → refresh reminder fire times
  ["f-evdate", "f-evtime"].forEach((id) =>
    document.getElementById(id).addEventListener("input", renderReminders));
  document.getElementById("f-ann-sched").addEventListener("change", () => {});
  document.getElementById("f-ann-now").addEventListener("change", () => {});
  // platforms
  ["f-pf-discord", "f-pf-telegram", "f-pf-groupme"].forEach((id) =>
    document.getElementById(id).addEventListener("change", () => {
      FormState.platforms = [];
      if (document.getElementById("f-pf-discord").checked) FormState.platforms.push("discord");
      if (document.getElementById("f-pf-telegram").checked) FormState.platforms.push("telegram");
      if (document.getElementById("f-pf-groupme").checked) FormState.platforms.push("groupme");
      updatePreview();
    }));
  // signature
  document.getElementById("sig-edit-link").addEventListener("click", (e) => {
    e.preventDefault();
    const wrap = document.getElementById("sig-edit-wrap");
    wrap.hidden = false;
    const ta = document.getElementById("sig-edit");
    ta.value = FormState.sigOverride != null ? FormState.sigOverride
      : (PL.getSetting(db, FormState.orgId, "signature.default", "") || "");
    ta.oninput = () => { FormState.sigOverride = ta.value; updateSignatureBox(); updatePreview(); };
  });
  document.getElementById("sig-reset").addEventListener("click", (e) => {
    e.preventDefault(); FormState.sigOverride = null;
    document.getElementById("sig-edit-wrap").hidden = true;
    updateSignatureBox(); updatePreview();
  });
  // preview toggle
  document.querySelectorAll("#pv-toggle button").forEach((b) =>
    b.addEventListener("click", () => {
      FormState.previewPlat = b.dataset.p;
      document.querySelectorAll("#pv-toggle button").forEach((x) => x.classList.toggle("on", x === b));
      ["discord", "telegram", "groupme"].forEach((p) => {
        const el = document.getElementById(`pv-${p}`);
        if (el) el.hidden = b.dataset.p !== p;
      });
    }));
  document.getElementById("f-cancel").addEventListener("click", () => { PS.view = "none"; renderRightPane(); });
  document.getElementById("f-submit").addEventListener("click", submitForm);
}

function setMode(m) {
  FormState.type = m;
  document.querySelectorAll("#mode-seg button").forEach((b) => b.classList.toggle("on", b.dataset.m === m));
  document.getElementById("grp-onetime").hidden = m !== "one_time";
  document.getElementById("grp-recurring").hidden = m !== "recurring";
  document.getElementById("grp-event").hidden = m !== "event";
  document.getElementById("grp-addkb").hidden = m === "event";  // events already file a KB item
  // default discord channel by type
  const key = m === "event" ? "default.channel.event" : "default.channel.broadcast";
  const ch = PL.getSetting(db, FormState.orgId, key, null);
  const sel = document.getElementById("f-dchan");
  if (ch && [...sel.options].some((o) => o.value === ch)) sel.value = ch;
  document.getElementById("f-submit").textContent =
    m === "recurring" ? "Create Recurring Post" : m === "event" ? "Schedule Event" : "Schedule Post";
  if (m === "recurring") updateOccPreview();
  if (m === "event") renderReminders();
  updatePreview();
}

function collectRecurrence() {
  const freq = { day: "daily", week: "weekly", month: "monthly" }[val("f-unit")] || "daily";
  const rec = { freq, interval: parseInt(val("f-interval") || "1", 10), time: val("f-rtime") || "09:00" };
  if (freq === "weekly") rec.days_of_week = FormState.days.slice();
  const start = val("f-start"); if (start) rec.start = start;
  if (!document.getElementById("f-noend").checked) { const end = val("f-end"); if (end) rec.end = end; }
  return rec;
}

function updateOccPreview() {
  const box = document.getElementById("occ-preview"); if (!box) return;
  const occ = PL.nextOccurrences(collectRecurrence(), new Date(), 3);
  box.innerHTML = occ.length
    ? "Next " + occ.length + " occurrences:<br/>" + occ.map((d) =>
        `<span class="occ">→ ${d.toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</span>`).join("<br/>")
    : "No occurrences — check the dates.";
}

function renderReminders() {
  const host = document.getElementById("rem-list"); if (!host) return;
  const evd = val("f-evdate"), evt = val("f-evtime");
  host.innerHTML = FormState.reminders.map((r, i) => {
    const fire = evd ? PL.reminderFireTime(evd, evt, r.offset, r.unit) : null;
    return `<div class="reminder">
      <input type="checkbox" class="rm-en" data-i="${i}" ${r.enabled ? "checked" : ""}>
      <div class="rm-when">
        <span class="inline">
          <input type="number" class="rm-off" data-i="${i}" value="${r.offset}" min="1" style="width:60px">
          <select class="rm-unit" data-i="${i}">
            ${["minutes", "hours", "days", "weeks"].map((u) => `<option ${u === r.unit ? "selected" : ""}>${u}</option>`).join("")}
          </select> before
        </span>
        <div class="rm-fire">${fire ? fire.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "set event date"}</div>
      </div>
      <div class="platpills">${["discord", "telegram", "groupme"].map((pf) =>
        `<span class="platpill ${r.platforms.includes(pf) ? "on" : ""}" data-i="${i}" data-pf="${pf}">${pf === "groupme" ? "G" : pf[0].toUpperCase()}</span>`).join("")}</div>
    </div>`;
  }).join("") + `<a href="#" id="add-rem" class="muted">+ Add reminder</a>`;

  host.querySelectorAll(".rm-en").forEach((el) => el.onchange = () => { FormState.reminders[el.dataset.i].enabled = el.checked; });
  host.querySelectorAll(".rm-off").forEach((el) => el.oninput = () => { FormState.reminders[el.dataset.i].offset = parseInt(el.value || "1", 10); renderReminders(); });
  host.querySelectorAll(".rm-unit").forEach((el) => el.onchange = () => { FormState.reminders[el.dataset.i].unit = el.value; renderReminders(); });
  host.querySelectorAll(".platpill").forEach((el) => el.onclick = () => {
    const r = FormState.reminders[el.dataset.i], pf = el.dataset.pf, idx = r.platforms.indexOf(pf);
    if (idx >= 0) r.platforms.splice(idx, 1); else r.platforms.push(pf);
    renderReminders();
  });
  const add = document.getElementById("add-rem");
  if (add) add.onclick = (e) => { e.preventDefault(); FormState.reminders.push({ enabled: true, offset: 1, unit: "days", platforms: FormState.platforms.slice() }); renderReminders(); };
}

function currentSignature() { return PL.renderSignature(db, FormState.orgId, FormState.sigOverride); }
function updateSignatureBox() {
  const sig = currentSignature();
  document.getElementById("sig-display").textContent = sig || "(no signature)";
}

function tgHtml(s) {
  s = esc(s);
  s = s.replace(/\*\*(.+?)\*\*|__(.+?)__/gs, (m, a, b) => `<b>${a || b}</b>`);
  s = s.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)/gs, (m, a, b) => `<i>${a || b}</i>`);
  return s;
}
function previewContent() {
  if (FormState.type === "event") {
    return `📅 ${val("f-evname") || "Event name"}\n${val("f-evdate") || "date"} at ${val("f-evtime") || "TBD"} · ${val("f-evloc") || ""}\n${val("f-content") || ""}`.trim();
  }
  return val("f-content");
}
function updatePreview() {
  const sig = currentSignature();
  const body = previewContent();
  const full = sig ? body + "\n\n" + sig : body;
  const dc = document.getElementById("pv-discord");
  const tg = document.getElementById("pv-telegram");
  const gm = document.getElementById("pv-groupme");
  if (dc) dc.innerHTML = `<div class="pv-author">GSA Gateway</div>` + esc(full);
  if (tg) tg.innerHTML = `<div class="pv-bubble">${tgHtml(full)}</div>`;
  if (gm) gm.innerHTML = `<div class="pv-groupme-bubble">${esc(full)}</div>`;
}

function submitForm() {
  const content = val("f-content").trim();
  const orgId = Number(val("f-org"));
  const platforms = FormState.platforms.slice();
  if (!platforms.length) { toast("Pick at least one platform", false); return; }
  const discordChannel = platforms.includes("discord") ? val("f-dchan") : null;
  const signature = FormState.sigOverride;
  const tz = PL.orgTimezone(db);
  // events already file their own KB item; the checkbox only applies to posts.
  const addToKb = FormState.type !== "event" && document.getElementById("f-addkb").checked;
  let patch, server = null, title = "Post ready to apply";

  if (FormState.type === "one_time") {
    if (!content) { toast("Content is required", false); return; }
    const now = document.getElementById("f-now").checked;
    const date = val("f-date"), time = val("f-time");
    if (!now && !date) { toast("Pick a date or check Send immediately", false); return; }
    const scheduledForUTC = now ? null : PL.localToUTC(`${date} ${time}`, tz);
    patch = PL.buildPostPatch({ orgId, content, platforms, discordChannel, signature,
      scheduledForUTC: scheduledForUTC || PL.nowUTC(), type: "one_time", sourceType: "manual", addToKb }, tz);
    server = { path: "/posts", body: { org_id: orgId, type: "one_time", content,
      channels: platforms, discord_channel: discordChannel, scheduled_for: scheduledForUTC,
      signature, source_type: "manual", add_to_kb: addToKb } };
  } else if (FormState.type === "recurring") {
    if (!content) { toast("Content is required", false); return; }
    const rec = collectRecurrence();
    const occ = PL.nextOccurrences(rec, new Date(), 1)[0];
    const nextRunUTC = occ ? PL.localToUTC(PL.fmtDateTime(occ), tz) : null;
    patch = PL.buildRecurringPatch({ orgId, name: preview(content, 40), content, recurrence: rec,
      platforms, discordChannel, signature, nextRunUTC, postType: "recurring_instance", addToKb }, tz);
    title = "Recurring post ready to apply";
  } else {
    const name = val("f-evname").trim(), date = val("f-evdate"), time = val("f-evtime") || "18:00", loc = val("f-evloc").trim();
    if (!name || !date) { toast("Event name and date are required", false); return; }
    const annNow = document.getElementById("f-ann-now").checked;
    const startUTC = PL.localToUTC(`${date} ${time}`, tz);
    const [dateUTC, timeUTCfull] = startUTC.split(" ");
    const announceUTC = annNow ? PL.nowUTC() : PL.localToUTC(`${val("f-ann-date")} ${val("f-ann-time") || "09:00"}`, tz);
    const announceContent = `📅 ${name}\n${date} at ${time} · ${loc}\n${content}`.trim();
    const kiContent = `${name} — ${date} at ${time}, ${loc}.` + (content ? "\n" + content : "");
    patch = PL.buildEventPatch({ orgId, name, dateUTC, timeUTC: timeUTCfull.slice(0, 5), location: loc,
      description: content, kiContent, announceContent, announceUTC, startUTC,
      platforms, discordChannel, signature, reminders: FormState.reminders }, tz);
    server = { path: "/posts", body: { org_id: orgId, type: "event", name, date: dateUTC,
      time: timeUTCfull.slice(0, 5), location: loc, description: content, ki_content: kiContent,
      announce_content: announceContent, announce_at: announceUTC, channels: platforms,
      discord_channel: discordChannel, signature,
      reminders: FormState.reminders.filter((r) => r.enabled).map((r) => ({ offset: r.offset, unit: r.unit, channels: r.platforms })) } };
    title = "Event ready to apply";
  }
  applyCreate(server, patch, title, { rebuild: addToKb }, () => { PS.view = "none"; renderPostsList(); renderRightPane(); });
}

// ═════════════════════════ Tab 3: Knowledge Base ═════════════════════════
const KB = { orgId: null, typeFilter: "all", search: "", page: 1, pageSize: 20, selectedId: null, view: "none", collapsed: {} };

const ORG_ICONS = { university: "🏛️", gsa: "📋", council: "📋", college: "🎓", department: "🏢", lab: "🔬", club: "👥", event_series: "🎯", person: "👤", office: "🏢", custom: "📁" };
const orgIcon = (t) => ORG_ICONS[t] || "📁";
const KTYPE = { faq: "k-faq", policy: "k-policy", contact: "k-contact", resource: "k-resource", event_info: "k-event", announcement: "k-ann", custom: "k-custom" };
const KB_TYPES = ["faq", "policy", "contact", "resource", "event_info", "announcement", "custom"];

function orgPath(orgId) {
  return PL.orgAncestors(db, orgId).map((id) => scalar("SELECT name FROM organizations WHERE id=?", [id])).reverse().join(" › ");
}

function renderKB() {
  if (KB.orgId === null)
    KB.orgId = scalar("SELECT id FROM organizations WHERE slug='gsa'") || scalar("SELECT id FROM organizations ORDER BY id LIMIT 1");
  const missing = PL.missingVectorCount(db);
  document.getElementById("tab-kb").innerHTML = `
    ${missing > 0 ? `<div class="rebuild-banner">⚠️ ${missing} new item${missing > 1 ? "s" : ""} need${missing > 1 ? "" : "s"} indexing. Run <code>python v2/scripts/rebuild_index.py</code> to make ${missing > 1 ? "them" : "it"} searchable by the bot.</div>` : ""}
    <div class="kb-wrap">
      <div class="kb-col kb-tree-col">
        <div class="kb-tree" id="kb-tree"></div>
        <div class="kb-tree-foot"><button class="btn btn-ghost btn-sm" id="add-org-btn">+ Add Organization</button></div>
      </div>
      <div class="kb-col kb-list-col" id="kb-list-col"></div>
      <div class="kb-col detail-pane" id="kb-detail"></div>
    </div>`;
  renderOrgTree();
  renderKBList();
  renderKBRight();
  document.getElementById("add-org-btn").onclick = openAddOrgModal;
}

// ── left: org tree ──────────────────────────────────────────────────────
function renderOrgTree() {
  const nodes = query("SELECT id,name,type,parent_id FROM organizations ORDER BY name");
  const byParent = {};
  nodes.forEach((n) => { (byParent[n.parent_id || 0] = byParent[n.parent_id || 0] || []).push(n); });
  const counts = {};
  query("SELECT org_id, COUNT(*) c FROM knowledge_items WHERE is_active=1 GROUP BY org_id").forEach((r) => (counts[r.org_id] = r.c));
  const build = (parentKey, depth) => (byParent[parentKey] || []).map((n) => {
    const hasKids = (byParent[n.id] || []).length > 0;
    const collapsed = KB.collapsed[n.id];
    return `<div class="tree-node">
      <div class="tree-row ${n.id === KB.orgId ? "sel" : ""}" data-id="${n.id}" style="padding-left:${8 + depth * 16}px">
        <span class="tree-caret" data-toggle="${n.id}">${hasKids ? (collapsed ? "▸" : "▾") : "·"}</span>
        <span class="tree-icon">${orgIcon(n.type)}</span>
        <span class="tree-name">${esc(n.name)}</span>
        <span class="tree-count">${counts[n.id] || 0}</span>
      </div>
      ${hasKids && !collapsed ? `<div class="tree-children">${build(n.id, depth + 1)}</div>` : ""}
    </div>`;
  }).join("");
  document.getElementById("kb-tree").innerHTML = build(0, 0);
  document.querySelectorAll("#kb-tree .tree-row").forEach((el) => el.onclick = (e) => {
    if (e.target.dataset.toggle != null) { const id = Number(e.target.dataset.toggle); KB.collapsed[id] = !KB.collapsed[id]; renderOrgTree(); return; }
    KB.orgId = Number(el.dataset.id); KB.page = 1; KB.selectedId = null; KB.view = "none";
    renderOrgTree(); renderKBList(); renderKBRight();
  });
}

// ── center: content list ────────────────────────────────────────────────
function renderKBList() {
  const orgName = scalar("SELECT name FROM organizations WHERE id=?", [KB.orgId]) || "—";
  const w = ["org_id=?", "is_active=1"], p = [KB.orgId];
  if (KB.typeFilter !== "all") { w.push("type=?"); p.push(KB.typeFilter); }
  if (KB.search) { w.push("(title LIKE ? OR content LIKE ?)"); p.push("%" + KB.search + "%", "%" + KB.search + "%"); }
  const clause = w.join(" AND ");
  const total = scalar(`SELECT COUNT(*) FROM knowledge_items WHERE ${clause}`, p) || 0;
  const pages = Math.max(1, Math.ceil(total / KB.pageSize));
  if (KB.page > pages) KB.page = pages;
  const rows = query(`SELECT id,type,title,content,version,created_at FROM knowledge_items WHERE ${clause} ORDER BY id DESC LIMIT ? OFFSET ?`, [...p, KB.pageSize, (KB.page - 1) * KB.pageSize]);

  document.getElementById("kb-list-col").innerHTML = `
    <div class="kb-list-head">
      <div class="kb-list-title"><strong>${esc(orgName)}</strong> <span class="muted">— ${total} item${total === 1 ? "" : "s"}</span></div>
      <div class="list-toolbar" style="border:0;padding:10px 0 0">
        <button class="btn btn-primary btn-sm" id="add-content-btn">+ Add Content</button>
        <select id="kb-type-filter">${["all", ...KB_TYPES].map((t) => `<option value="${t}" ${t === KB.typeFilter ? "selected" : ""}>${t === "all" ? "All types" : t}</option>`).join("")}</select>
        <input id="kb-search" type="text" placeholder="Search…" value="${esc(KB.search)}">
      </div>
    </div>
    <div class="plist" id="kb-items">${rows.length ? rows.map(kbItem).join("") : `<div class="panel-empty">No content.</div>`}</div>
    <div class="pager">
      <button id="kb-prev" ${KB.page <= 1 ? "disabled" : ""}>←</button> Page ${KB.page} of ${pages} <button id="kb-next" ${KB.page >= pages ? "disabled" : ""}>→</button>
    </div>`;

  document.getElementById("add-content-btn").onclick = () => { KB.view = "form"; KB.formMode = "add"; KB.selectedId = null; renderKBRight(); };
  document.getElementById("kb-type-filter").onchange = (e) => { KB.typeFilter = e.target.value; KB.page = 1; renderKBList(); };
  let deb; document.getElementById("kb-search").oninput = (e) => { clearTimeout(deb); deb = setTimeout(() => { KB.search = e.target.value.trim(); KB.page = 1; renderKBList(); }, 200); };
  const pv = document.getElementById("kb-prev"), nx = document.getElementById("kb-next");
  if (pv) pv.onclick = () => { KB.page--; renderKBList(); };
  if (nx) nx.onclick = () => { KB.page++; renderKBList(); };
  document.querySelectorAll("#kb-items .plist-item").forEach((el) => el.onclick = () => { KB.selectedId = Number(el.dataset.id); KB.view = "detail"; renderKBList(); renderKBRight(); });
}

function kbItem(it) {
  return `<div class="plist-item ${it.id === KB.selectedId ? "selected" : ""}" data-id="${it.id}">
    <div class="row-main">
      <div class="plist-meta"><span class="ktype ${KTYPE[it.type] || "k-custom"}">${esc(it.type)}</span>${it.version > 1 ? `<span class="vbadge">v${it.version}</span>` : ""}</div>
      <div class="row-title" style="margin-top:5px">${esc(it.title || preview(it.content, 56))}</div>
      <div class="row-meta">${absTime(it.created_at)}</div>
    </div>
  </div>`;
}

// ── right: detail / form / history ──────────────────────────────────────
function renderKBRight() {
  if (KB.view === "form") return kbForm();
  if (KB.view === "history" && KB.selectedId) return kbHistory(KB.selectedId);
  if (KB.view === "detail" && KB.selectedId) return kbDetail(KB.selectedId);
  document.getElementById("kb-detail").innerHTML = `<div class="detail-placeholder">Select a knowledge item to view<br/>or click <strong>+ Add Content</strong>.</div>`;
}

function kbDetail(id) {
  const it = one("SELECT * FROM knowledge_items WHERE id=?", [id]);
  if (!it) { KB.view = "none"; return renderKBRight(); }
  let meta = {}; try { meta = JSON.parse(it.metadata || "{}"); } catch (e) {}
  const metaRows = Object.keys(meta).length
    ? `<div class="section-label">Metadata</div><dl class="kv">${Object.entries(meta).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</dd>`).join("")}</dl>` : "";
  const versions = scalar("SELECT COUNT(*) FROM knowledge_items WHERE root_id=?", [it.root_id || it.id]) || 1;
  document.getElementById("kb-detail").innerHTML = `
    <div class="detail-head">
      <span class="ktype ${KTYPE[it.type] || "k-custom"}" style="align-self:center">${esc(it.type)}</span>
      <h2>${esc(it.title || preview(it.content, 60))}</h2>
      <div class="actions">
        <button class="btn btn-ghost btn-sm" id="kb-edit">Edit</button>
        <button class="btn btn-danger btn-sm" id="kb-deact">Deactivate</button>
      </div>
    </div>
    ${versions > 1 ? `<a href="#" id="kb-hist" class="muted">Version history (${versions} versions)</a>` : ""}
    <div class="section-label">Content</div>
    <div class="content-box">${esc(it.content)}</div>
    ${metaRows}
    <div class="section-label">Details</div>
    <dl class="kv">
      <dt>Organization</dt><dd>${esc(orgPath(it.org_id))}</dd>
      <dt>Type</dt><dd>${esc(it.type)}</dd>
      <dt>Version</dt><dd>v${it.version}</dd>
      <dt>Created</dt><dd>${absTime(it.created_at)} by ${esc(it.created_by || "—")}</dd>
      ${it.updated_at ? `<dt>Updated</dt><dd>${absTime(it.updated_at)}</dd>` : ""}
      ${it.source_url ? `<dt>Source URL</dt><dd><a href="${esc(it.source_url)}" target="_blank">${esc(it.source_url)}</a></dd>` : ""}
    </dl>`;
  document.getElementById("kb-edit").onclick = () => { KB.view = "form"; KB.formMode = "edit"; renderKBRight(); };
  document.getElementById("kb-deact").onclick = () => {
    applyAndExport(`UPDATE knowledge_items SET is_active=0, updated_at=${PL.sqlLit(PL.nowUTC())} WHERE id=${id};`,
      "Deactivate item", { type: "knowledge_item", rebuild: true });
    KB.selectedId = null; KB.view = "none"; renderOrgTree(); renderKBList(); renderKBRight();
  };
  const h = document.getElementById("kb-hist");
  if (h) h.onclick = (e) => { e.preventDefault(); KB.view = "history"; renderKBRight(); };
}

function kbHistory(id) {
  const it = one("SELECT root_id FROM knowledge_items WHERE id=?", [id]);
  const root = it.root_id || id;
  const versions = query("SELECT id,version,is_active,created_at FROM knowledge_items WHERE root_id=? ORDER BY version DESC", [root]);
  document.getElementById("kb-detail").innerHTML = `
    <div class="detail-head"><h2>Version History</h2><div class="actions"><button class="btn btn-ghost btn-sm" id="kb-back">← Back</button></div></div>
    <div class="vtimeline">${versions.map((v) => `
      <div class="vrow ${v.is_active ? "active" : ""}">
        <div><strong>v${v.version}</strong> ${v.is_active ? '<span class="badge sent">current</span>' : '<span class="badge cancelled">superseded</span>'}</div>
        <div class="row-meta">${absTime(v.created_at)}</div>
        <div class="vactions">
          <a href="#" data-view="${v.id}">View</a>
          ${!v.is_active ? `<a href="#" data-restore="${v.id}">Restore</a>` : ""}
        </div>
      </div>`).join("")}</div>`;
  document.getElementById("kb-back").onclick = () => { KB.view = "detail"; renderKBRight(); };
  document.querySelectorAll("#kb-detail [data-view]").forEach((el) => el.onclick = (e) => { e.preventDefault(); KB.selectedId = Number(el.dataset.view); KB.view = "detail"; renderKBRight(); });
  document.querySelectorAll("#kb-detail [data-restore]").forEach((el) => el.onclick = (e) => {
    e.preventDefault();
    const oldV = one("SELECT * FROM knowledge_items WHERE id=?", [Number(el.dataset.restore)]);
    const cur = one("SELECT * FROM knowledge_items WHERE root_id=? AND is_active=1", [oldV.root_id || oldV.id]);
    let meta = {}; try { meta = JSON.parse(oldV.metadata || "{}"); } catch (err) {}
    const patch = PL.buildKnowledgeItemPatch({ type: oldV.type, title: oldV.title, content: oldV.content,
      metadata: meta, sourceUrl: oldV.source_url, editOld: cur }, PL.orgTimezone(db));
    db.exec(patch); KB.view = "none"; renderKB();
    showPatchModal("Restore version", patch, { rebuild: true });
  });
}

function kbForm() {
  const editing = KB.formMode === "edit" && KB.selectedId;
  const it = editing ? one("SELECT * FROM knowledge_items WHERE id=?", [KB.selectedId]) : null;
  let meta = {}; if (it) { try { meta = JSON.parse(it.metadata || "{}"); } catch (e) {} }
  const type = it ? it.type : "faq";
  // type list comes from the deployment's vocab (inherited from root), fallback hardcoded
  const kTypes = PL.getSettingJSON(db, KB.orgId, "vocab.knowledge_types", KB_TYPES);
  document.getElementById("kb-detail").innerHTML = `
   <div class="form">
    <h2>${editing ? "Edit Content" : "Add Content"}</h2>
    <div class="field"><label>Organization</label><input type="text" value="${esc(orgPath(KB.orgId))}" readonly></div>
    <div class="field"><label>Type</label><select id="k-type">${kTypes.map((t) => `<option value="${t}" ${t === type ? "selected" : ""}>${t}</option>`).join("")}</select></div>
    <div class="field"><label>Title</label><input type="text" id="k-title" value="${esc(it ? it.title || "" : "")}"></div>
    <div class="field"><label>Content</label><textarea id="k-content" rows="8" placeholder="Write in plain text. No markdown needed.">${esc(it ? it.content : "")}</textarea></div>
    <div id="k-meta-fields"></div>
    <div class="field"><label>Source URL (optional)</label><input type="text" id="k-source" value="${esc(it ? it.source_url || "" : "")}"></div>
    <div class="form-buttons">
      <button class="btn btn-ghost" id="k-cancel">Cancel</button>
      <button class="btn btn-primary" id="k-save">Save Content</button>
    </div>
   </div>`;
  const renderMetaFields = () => {
    const t = val("k-type");
    const host = document.getElementById("k-meta-fields");
    if (t === "contact") {
      const roles = PL.getSettingJSON(db, KB.orgId, "vocab.contact_roles",
        ["officer", "professor", "staff", "custom"]);
      const cur = meta.role || "";
      const isStd = !!cur && roles.includes(cur);
      const sel = isStd ? cur : (cur ? "custom" : roles[0]);
      host.innerHTML = `<div class="section-label">Contact fields → metadata</div>
        <div class="inline">
          <div class="field" style="flex:1"><label>Email</label><input type="text" id="m-email" value="${esc(meta.email || "")}"></div>
          <div class="field" style="flex:1"><label>Phone</label><input type="text" id="m-phone" value="${esc(meta.phone || "")}"></div>
        </div>
        <div class="inline">
          <div class="field" style="flex:1"><label>Office</label><input type="text" id="m-office" value="${esc(meta.office || "")}"></div>
          <div class="field" style="flex:1"><label>Role</label>
            <select id="m-role">${roles.map((r) => `<option value="${r}" ${r === sel ? "selected" : ""}>${r}</option>`).join("")}</select>
            <input type="text" id="m-role-custom" placeholder="custom role" value="${esc(isStd ? "" : cur)}" ${sel === "custom" ? "" : "hidden"} style="margin-top:6px">
          </div>
        </div>`;
      const roleSel = document.getElementById("m-role");
      roleSel.onchange = () => { document.getElementById("m-role-custom").hidden = roleSel.value !== "custom"; };
    } else if (t === "resource") {
      host.innerHTML = `<div class="section-label">Resource fields → metadata</div>
        <div class="field"><label>URL</label><input type="text" id="m-url" value="${esc(meta.url || "")}"></div>
        <div class="field"><label>Category</label><input type="text" id="m-category" value="${esc(meta.category || "")}"></div>`;
    } else {
      host.innerHTML = `<div class="section-label">Metadata (optional JSON)</div>
        <div class="field"><textarea id="m-json" rows="2" placeholder="{}">${esc(Object.keys(meta).length ? JSON.stringify(meta) : "")}</textarea></div>`;
    }
  };
  renderMetaFields();
  document.getElementById("k-type").onchange = renderMetaFields;
  document.getElementById("k-cancel").onclick = () => { KB.view = KB.selectedId ? "detail" : "none"; renderKBRight(); };
  document.getElementById("k-save").onclick = () => saveKBContent(editing, it);
}

function collectMeta() {
  const t = val("k-type");
  if (t === "contact") {
    const m = {}; ["email", "phone", "office"].forEach((k) => { const v = val("m-" + k); if (v) m[k] = v; });
    let role = val("m-role");
    if (role === "custom") role = val("m-role-custom").trim();
    if (role && role !== "custom") m.role = role;
    return m;
  }
  if (t === "resource") {
    const m = {}; if (val("m-url")) m.url = val("m-url"); if (val("m-category")) m.category = val("m-category"); return m;
  }
  const raw = val("m-json").trim();
  if (!raw) return {};
  try { return JSON.parse(raw); } catch (e) { return {}; }
}

function saveKBContent(editing, it) {
  const content = val("k-content").trim();
  if (!content) { toast("Content is required", false); return; }
  const tz = PL.orgTimezone(db);
  const data = { type: val("k-type"), title: val("k-title").trim() || null, content,
    metadata: collectMeta(), sourceUrl: val("k-source").trim() || null };
  const patch = editing
    ? PL.buildKnowledgeItemPatch({ ...data, editOld: it }, tz)
    : PL.buildKnowledgeItemPatch({ orgId: KB.orgId, ...data }, tz);
  // server endpoint creates new items; versioned edits fall back to a patch.
  const server = editing ? null : { path: "/knowledge", body: {
    org_id: KB.orgId, type: data.type, title: data.title, content: data.content,
    metadata: data.metadata, source_url: data.sourceUrl } };
  applyCreate(server, patch, editing ? "Content update ready" : "Content ready to apply",
    { rebuild: true }, () => { KB.view = "none"; renderKB(); });
}

// ── add organization modal ──────────────────────────────────────────────
// Clean slug for a new org: prefer a parenthetical acronym in the name
// (e.g. "Graduate Women in Computing Society (GWICS)" -> "gwics"), else slugify the
// whole name. The router resolves orgs by slug, so a clean short slug means the acronym
// "just works" — exactly like GSA's slug is 'gsa'.
function smartOrgSlug(name) {
  const m = (name || "").match(/\(([^)]{2,})\)/);
  return PL.slugify(m ? m[1] : (name || ""));
}

function openAddOrgModal() {
  const orgs = query(
    "WITH RECURSIVE t(id,name,parent_id,depth) AS (SELECT id,name,parent_id,0 FROM organizations WHERE parent_id IS NULL " +
    "UNION ALL SELECT o.id,o.name,o.parent_id,t.depth+1 FROM organizations o JOIN t ON o.parent_id=t.id) SELECT id,name,depth FROM t ORDER BY depth,name");
  const types = PL.getSettingJSON(db, KB.orgId || 1, "vocab.org_types",
    ["university", "gsa", "college", "department", "club", "custom"]);
  document.getElementById("modal-body").innerHTML = `
    <h2>Add Organization</h2>
    <div class="field"><label>Parent</label><select id="o-parent">${orgs.map((o) => `<option value="${o.id}" ${o.id === KB.orgId ? "selected" : ""}>${"   ".repeat(o.depth)}${esc(o.name)}</option>`).join("")}</select></div>
    <div class="field"><label>Name</label><input type="text" id="o-name" placeholder="e.g. Graduate Women in Computing Society (GWICS)"></div>
    <div class="field"><label>Slug</label><input type="text" id="o-slug" placeholder="auto from name — e.g. gwics"><div class="muted" style="font-size:12px;margin-top:2px">short id used to find the org (e.g. acronym). Auto-fills from the name; edit if you like.</div></div>
    <div class="field"><label>Type</label><select id="o-type">${types.map((t) => `<option value="${t}" ${t === "club" ? "selected" : ""}>${t}</option>`).join("")}</select></div>
    <div class="field"><label>Description</label><textarea id="o-desc" rows="2"></textarea></div>
    <div class="modal-actions"><button class="btn btn-ghost" id="o-cancel">Cancel</button><button class="btn btn-primary" id="o-save">Save</button></div>`;
  document.getElementById("modal").hidden = false;
  document.getElementById("o-cancel").onclick = closeModal;
  // auto-fill the slug from the name until the admin edits the slug themselves
  let slugEdited = false;
  const slugEl = document.getElementById("o-slug");
  slugEl.oninput = () => { slugEdited = true; };
  document.getElementById("o-name").oninput = (e) => {
    if (!slugEdited) slugEl.value = smartOrgSlug(e.target.value.trim());
  };
  document.getElementById("o-save").onclick = () => {
    const name = val("o-name").trim();
    if (!name) { toast("Name is required", false); return; }
    const parentId = Number(val("o-parent"));
    let slug = (val("o-slug").trim() || smartOrgSlug(name)), base = slug, i = 2;
    while (query("SELECT 1 FROM organizations WHERE slug=?", [slug]).length) slug = base + "-" + (i++);
    const type = val("o-type"), description = val("o-desc").trim();
    const patch = PL.buildOrgPatch({ parentId, name, slug, type, description }, PL.orgTimezone(db));
    const server = { path: "/orgs", body: { name, slug, type, parent_id: parentId, description } };
    applyCreate(server, patch, "Organization ready to apply", {}, () => { KB.collapsed = {}; renderKB(); });
  };
}

// ═════════════════════════ Tab 5: Analytics ═════════════════════════
const AN = { range: 30 }; // null = all time
const _charts = {};
function makeChart(id, config) {
  if (_charts[id]) { _charts[id].destroy(); }
  const el = document.getElementById(id);
  if (el && window.Chart) _charts[id] = new Chart(el, config);
}
const NJIT_RED = "#CC0000";
function periodClause(col) {
  return AN.range ? `DATE(${col}) >= DATE('now','-${AN.range} days')` : "1=1";
}

function renderAnalytics() {
  const ranges = [[7, "7 days"], [30, "30 days"], [90, "90 days"], [null, "All time"]];
  const pq = periodClause("timestamp");

  const total = scalar(`SELECT COUNT(*) FROM questions WHERE ${pq}`) || 0;
  const answered = scalar(`SELECT SUM(confidence>=50) FROM questions WHERE ${pq}`) || 0;
  const rate = total ? Math.round((answered / total) * 1000) / 10 : 0;
  const users = scalar(`SELECT COUNT(DISTINCT user_id_hash) FROM questions WHERE ${pq}`) || 0;

  const fUp = scalar(`SELECT COUNT(*) FROM response_feedback WHERE rating='thumbs_up' AND ${periodClause("timestamp")}`) || 0;
  const fDown = scalar(`SELECT COUNT(*) FROM response_feedback WHERE rating='thumbs_down' AND ${periodClause("timestamp")}`) || 0;
  const fRegen = scalar(`SELECT COUNT(*) FROM response_feedback WHERE rating='regenerate' AND ${periodClause("timestamp")}`) || 0;
  const sat = (fUp + fDown) ? Math.round((fUp / (fUp + fDown)) * 1000) / 10 : 0;

  const sentClause = periodClause("sent_at");
  const postsSent = scalar(`SELECT COUNT(*) FROM posts WHERE sent_at IS NOT NULL AND ${sentClause}`) || 0;
  const delivOk = scalar(`SELECT COUNT(*) FROM post_deliveries WHERE status='success' AND ${periodClause("sent_at")}`) || 0;
  const delivFail = scalar(`SELECT COUNT(*) FROM post_deliveries WHERE status='failed' AND ${periodClause("sent_at")}`) || 0;

  const kbTotal = scalar("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1") || 0;
  const byOrg = query("SELECT o.name, COUNT(*) c FROM knowledge_items ki JOIN organizations o ON o.id=ki.org_id WHERE ki.is_active=1 GROUP BY o.id ORDER BY c DESC");
  const byType = query("SELECT type, COUNT(*) c FROM knowledge_items WHERE is_active=1 GROUP BY type ORDER BY c DESC");
  const missing = PL.missingVectorCount(db);

  const unanswered = query(
    `SELECT question_text, COUNT(*) n, MAX(timestamp) last_asked, ROUND(AVG(confidence),1) avg_c
     FROM questions WHERE confidence<50 AND ${pq} GROUP BY question_text ORDER BY n DESC LIMIT 10`);
  const postsByType = query(
    `SELECT p.type, COUNT(*) total, SUM(CASE WHEN pd.status='success' THEN 1 ELSE 0 END) success
     FROM posts p LEFT JOIN post_deliveries pd ON p.id=pd.post_id
     WHERE p.sent_at IS NOT NULL AND ${periodClause("p.sent_at")} GROUP BY p.type ORDER BY total DESC`);

  document.getElementById("tab-analytics").innerHTML = `
    <div class="range-bar">${ranges.map(([r, l]) =>
      `<button class="range-btn ${AN.range === r ? "on" : ""}" data-r="${r}">${l}</button>`).join("")}</div>

    <div class="an-section"><h3>Questions</h3>
      <div class="stat-grid">
        ${statCard("Total Questions", total, "in period", true)}
        ${statCard("Answered Well", answered, "confidence ≥ 50")}
        ${statCard("Answer Rate", `${rate}<span class="unit">%</span>`, "")}
        ${statCard("Unique Users", users, "distinct askers")}
      </div>
      <div class="chart-card"><h4>Questions over time</h4><canvas id="chart-q" width="1160" height="200"></canvas></div>
      <div class="panel" style="margin-top:16px"><h3>Questions to Add to KB <span class="muted">(confidence &lt; 50)</span></h3>
        <div class="panel-body" style="max-height:none">
          <table class="deliv-table"><thead><tr><th>Question</th><th>Asked</th><th>Last asked</th><th>Avg conf.</th></tr></thead>
          <tbody>${unanswered.length ? unanswered.map((u) => `<tr><td>${esc(u.question_text)}</td><td>${u.n}×</td><td>${absTime(u.last_asked)}</td><td>${u.avg_c}</td></tr>`).join("") : `<tr><td colspan="4" class="muted">No unanswered questions in period.</td></tr>`}</tbody></table>
        </div></div>
    </div>

    <div class="an-section"><h3>Feedback</h3>
      <div class="stat-grid stat-3">
        ${statCard("👍 Helpful", fUp, "")}
        ${statCard("👎 Not helpful", fDown, fRegen ? `🔄 ${fRegen} regenerate` : "")}
        ${statCard("Satisfaction", `${sat}<span class="unit">%</span>`, "up / (up+down)")}
      </div>
      <div class="chart-card"><h4>Feedback over time</h4><canvas id="chart-f" width="1160" height="170"></canvas></div>
    </div>

    <div class="an-section"><h3>Posts</h3>
      <div class="stat-grid stat-3">
        ${statCard("Posts Sent", postsSent, "in period")}
        ${statCard("Deliveries OK", delivOk, "")}
        ${statCard("Deliveries Failed", delivFail, "")}
      </div>
      <div class="panel" style="margin-top:14px"><h3>Posts by type</h3><div class="panel-body" style="max-height:none">
        <table class="deliv-table"><thead><tr><th>Type</th><th>Count</th><th>Success rate</th></tr></thead>
        <tbody>${postsByType.length ? postsByType.map((p) => `<tr><td>${iconFor(p.type)} ${esc(p.type)}</td><td>${p.total}</td><td>${p.total ? Math.round((p.success / p.total) * 100) : 0}%</td></tr>`).join("") : `<tr><td colspan="3" class="muted">No posts sent in period yet.</td></tr>`}</tbody></table>
      </div></div>
    </div>

    <div class="an-section"><h3>Knowledge Base</h3>
      <div class="kb-stats">
        <div><span class="muted">Total items</span><div class="big">${kbTotal}</div></div>
        <div><span class="muted">By org</span><div>${byOrg.map((o) => `${esc(o.name.split(" ").pop())} <b>${o.c}</b>`).join(" · ")}</div></div>
        <div><span class="muted">By type</span><div>${byType.map((t) => `${esc(t.type)} <b>${t.c}</b>`).join(" · ")}</div></div>
        <div><span class="muted">Needing reindex</span><div class="big ${missing ? "warn-text" : ""}">${missing}</div></div>
      </div>
      <div class="reindex-row">
        <code id="reindex-cmd">python v2/scripts/rebuild_index.py</code>
        <button class="btn btn-ghost btn-sm" id="copy-cmd">Copy to clipboard</button>
      </div>
    </div>`;

  document.querySelectorAll(".range-btn").forEach((b) => b.onclick = () => {
    AN.range = b.dataset.r === "null" ? null : Number(b.dataset.r);
    renderAnalytics();
  });
  const copy = document.getElementById("copy-cmd");
  copy.onclick = () => { navigator.clipboard && navigator.clipboard.writeText("python v2/scripts/rebuild_index.py"); toast("Command copied"); };

  if (new URLSearchParams(location.search).get("nochart")) return; // headless screenshot guard
  // charts
  const qDays = query(`SELECT DATE(timestamp) day, COUNT(*) c FROM questions WHERE ${pq} GROUP BY day ORDER BY day`);
  makeChart("chart-q", {
    type: "line",
    data: { labels: qDays.map((r) => r.day), datasets: [{ label: "Questions", data: qDays.map((r) => r.c), borderColor: NJIT_RED, backgroundColor: "rgba(204,0,0,.08)", fill: true, tension: .25, pointRadius: 2 }] },
    options: { responsive: false, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
  });
  const fDays = query(`SELECT DATE(timestamp) day, SUM(rating='thumbs_up') up, SUM(rating='thumbs_down') down FROM response_feedback WHERE ${periodClause("timestamp")} GROUP BY day ORDER BY day`);
  makeChart("chart-f", {
    type: "bar",
    data: { labels: fDays.map((r) => r.day), datasets: [
      { label: "👍", data: fDays.map((r) => r.up), backgroundColor: "#16a34a", stack: "s" },
      { label: "👎", data: fDays.map((r) => r.down), backgroundColor: NJIT_RED, stack: "s" }] },
    options: { responsive: false, maintainAspectRatio: false, animation: false, scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } } } },
  });
}

// ═════════════════════════ Tab 6: Settings ═════════════════════════
const SETTINGS_CATS = [
  { id: "org", name: "Organization Info" },
  { id: "sig", name: "Signature" },
  { id: "chan", name: "Default Channels" },
  { id: "notif", name: "Notification Defaults" },
  { id: "retr", name: "Retriever Config" },
  { id: "feat", name: "Features" },
  { id: "plat", name: "Platform Config" },
];
const SET = { cat: "org" };

const catMatch = {
  org: (k) => k.startsWith("org."),
  sig: (k) => k.startsWith("signature."),
  chan: (k) => k.startsWith("default.channel.") || k === "default.platforms" || k === "default.send_time",
  notif: (k) => k === "reminders.default",
  retr: (k) => k.startsWith("retriever."),
  feat: (k) => k.startsWith("feature."),
  plat: (k) => k === "org.telegram_channel" || k === "org.groupme_group",
};

function settingsRows(cat) {
  return query("SELECT id,org_id,key,value,type,description FROM settings ORDER BY key").filter((r) => catMatch[cat](r.key));
}

function renderSettings() {
  document.getElementById("tab-settings").innerHTML = `
    <div class="settings-wrap">
      <div class="kb-col settings-cats">
        ${SETTINGS_CATS.map((c) => `<div class="set-cat ${c.id === SET.cat ? "sel" : ""}" data-cat="${c.id}">${esc(c.name)}</div>`).join("")}
      </div>
      <div class="kb-col settings-editor" id="settings-editor"></div>
    </div>`;
  document.querySelectorAll(".set-cat").forEach((el) => el.onclick = () => { SET.cat = el.dataset.cat; renderSettings(); });
  renderSettingsEditor();
}

function settingInput(r) {
  const id = `set-${r.org_id}-${r.key.replace(/\W/g, "_")}`;
  if (r.type === "bool") {
    const on = String(r.value).toLowerCase() === "true";
    return `<label class="switch"><input type="checkbox" id="${id}" data-org="${r.org_id}" data-key="${esc(r.key)}" data-type="bool" ${on ? "checked" : ""}><span class="slider"></span></label>`;
  }
  const t = r.type === "int" ? "number" : "text";
  if (r.type === "json") {
    return `<textarea class="set-input" id="${id}" data-org="${r.org_id}" data-key="${esc(r.key)}" data-type="json" rows="2">${esc(r.value || "")}</textarea>`;
  }
  return `<input type="${t}" class="set-input" id="${id}" data-org="${r.org_id}" data-key="${esc(r.key)}" data-type="${esc(r.type)}" value="${esc(r.value || "")}">`;
}

function genericEditor(rows, title) {
  return `<h2>${esc(title)}</h2>
    ${rows.map((r) => `<div class="set-row">
      <div class="set-label"><div>${esc(r.description || r.key)}</div><div class="set-key">${esc(r.key)}</div></div>
      <div class="set-control">${settingInput(r)}</div>
    </div>`).join("")}
    <div class="form-buttons" style="justify-content:flex-end"><button class="btn btn-primary" id="set-save">Save All</button></div>`;
}

function settingUpdateStmt(orgId, key, value) {
  return `UPDATE settings SET value=${PL.sqlLit(value)}, updated_at=${PL.sqlLit(PL.nowUTC())}, ` +
    `updated_by='dashboard' WHERE org_id=${Number(orgId)} AND key=${PL.sqlLit(key)};`;
}
function wireGenericSave() {
  document.getElementById("set-save").onclick = () => {
    const changes = [];
    document.querySelectorAll("#settings-editor [data-key]").forEach((el) => {
      const value = el.dataset.type === "bool" ? (el.checked ? "true" : "false") : el.value;
      changes.push({ org_id: Number(el.dataset.org), key: el.dataset.key, value });
    });
    if (!changes.length) { toast("Nothing to save", false); return; }
    if (SERVER_URL) {
      Promise.all(changes.map((ch) => serverFetch("/settings", { method: "POST", body: ch })))
        .then(() => { toast("Settings applied ✅"); reloadFromServer(); })
        .catch((e) => toast("Server error: " + e.message, false));
      return;
    }
    applyAndExport(changes.map((ch) => settingUpdateStmt(ch.org_id, ch.key, ch.value)).join("\n"),
      "Settings ready to apply", { type: "settings" });
  };
}

function renderSettingsEditor() {
  const host = document.getElementById("settings-editor");
  const cat = SET.cat;
  if (cat === "sig") return renderSignatureSettings(host);
  if (cat === "notif") return renderReminderSettings(host);
  if (cat === "plat") return renderPlatformSettings(host);
  const rows = settingsRows(cat);
  host.innerHTML = genericEditor(rows, SETTINGS_CATS.find((c) => c.id === cat).name);
  if (cat === "retr") {
    host.insertAdjacentHTML("beforeend", `<p class="muted" style="margin-top:8px">Higher = those item types surface more often in search. Default: contact 1.5, event 1.2.</p>`);
  }
  wireGenericSave(rows);
}

function renderSignatureSettings(host) {
  const row = settingsRows("sig").find((r) => r.key === "signature.default");
  const varsRow = settingsRows("sig").find((r) => r.key === "signature.variables");
  let vars = {}; try { vars = JSON.parse(varsRow ? varsRow.value : "{}"); } catch (e) {}
  host.innerHTML = `<h2>Signature</h2>
    <div class="field"><label>Default signature template</label>
      <textarea id="sig-tmpl" data-org="${row.org_id}" rows="3">${esc(row.value || "")}</textarea></div>
    <div class="section-label">Preview</div>
    <div class="sig-box" id="sig-prev"></div>
    <div class="section-label">Available variables</div>
    <div class="varlist">${Object.entries(vars).map(([k, v]) => `<div><code>{${esc(k)}}</code> → ${esc(v)}</div>`).join("")}</div>
    <div class="form-buttons" style="justify-content:flex-end"><button class="btn btn-primary" id="sig-save">Save</button></div>`;
  const render = () => {
    const tmpl = val("sig-tmpl");
    document.getElementById("sig-prev").textContent = tmpl.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m)) || "(empty)";
  };
  document.getElementById("sig-tmpl").addEventListener("input", render);
  render();
  document.getElementById("sig-save").onclick = () => {
    applyAndExport(settingUpdateStmt(row.org_id, "signature.default", val("sig-tmpl")),
      "Signature ready to apply", { type: "settings",
        server: { path: "/settings", body: { org_id: row.org_id, key: "signature.default", value: val("sig-tmpl") } } });
  };
}

function renderReminderSettings(host) {
  const row = settingsRows("notif")[0];
  let rems = []; try { rems = JSON.parse(row.value || "[]"); } catch (e) {}
  const draw = () => {
    host.innerHTML = `<h2>Notification Defaults</h2>
      <p class="muted">Default reminders applied to new events.</p>
      <div id="nrem-list">${rems.map((r, i) => `
        <div class="reminder">
          <div class="rm-when"><strong>${r.offset} ${r.unit}</strong> before</div>
          <div class="platpills">${["discord", "telegram", "groupme"].map((pf) => `<span class="platpill ${(r.channels || []).includes(pf) ? "on" : ""}" data-i="${i}" data-pf="${pf}">${pf === "groupme" ? "G" : pf[0].toUpperCase()}</span>`).join("")}</div>
          <a href="#" class="muted nrem-del" data-i="${i}">Remove</a>
        </div>`).join("")}</div>
      <a href="#" id="nrem-add" class="muted">+ Add reminder</a>
      <div class="form-buttons" style="justify-content:flex-end"><button class="btn btn-primary" id="nrem-save">Save</button></div>`;
    host.querySelectorAll(".platpill").forEach((el) => el.onclick = () => {
      const r = rems[el.dataset.i]; r.channels = r.channels || [];
      const idx = r.channels.indexOf(el.dataset.pf);
      if (idx >= 0) r.channels.splice(idx, 1); else r.channels.push(el.dataset.pf);
      draw();
    });
    host.querySelectorAll(".nrem-del").forEach((el) => el.onclick = (e) => { e.preventDefault(); rems.splice(el.dataset.i, 1); draw(); });
    document.getElementById("nrem-add").onclick = (e) => { e.preventDefault(); rems.push({ offset: 1, unit: "days", channels: ["discord"] }); draw(); };
    document.getElementById("nrem-save").onclick = () => {
      applyAndExport(settingUpdateStmt(row.org_id, "reminders.default", JSON.stringify(rems)),
        "Reminder defaults ready to apply", { type: "settings",
          server: { path: "/settings", body: { org_id: row.org_id, key: "reminders.default", value: JSON.stringify(rems) } } });
    };
  };
  draw();
}

function renderPlatformSettings(host) {
  const gsaId = scalar("SELECT id FROM organizations WHERE slug='gsa'")
    || scalar("SELECT id FROM organizations ORDER BY id LIMIT 1");
  const tg = query("SELECT id,org_id,key,value FROM settings WHERE org_id=? AND key='org.telegram_channel'", [gsaId])[0];
  const gm = query("SELECT id,org_id,key,value FROM settings WHERE org_id=? AND key='org.groupme_group'", [gsaId])[0];
  const tgVal = tg ? tg.value : "";
  const gmVal = gm ? gm.value : "GSAGateWayNJIT";
  host.innerHTML = `<h2>Platform Config</h2>
    <div class="set-row"><div class="set-label"><div>Telegram channel</div><div class="set-key">org.telegram_channel</div></div>
      <div class="set-control"><input type="text" class="set-input" id="plat-tg" value="${esc(tgVal)}"></div></div>
    <div class="set-row"><div class="set-label"><div>GroupMe group</div><div class="set-key">org.groupme_group</div></div>
      <div class="set-control"><input type="text" class="set-input" id="plat-gm" value="${esc(gmVal)}"></div></div>
    <div class="set-row"><div class="set-label"><div>Discord bot token</div><div class="set-key">.env DISCORD_TOKEN</div></div>
      <div class="set-control"><input type="text" class="set-input" value="••••••••••••" readonly></div></div>
    <div class="set-row"><div class="set-label"><div>Telegram bot token</div><div class="set-key">.env TELEGRAM_TOKEN</div></div>
      <div class="set-control"><input type="text" class="set-input" value="••••••••••••" readonly></div></div>
    <div class="set-row"><div class="set-label"><div>GroupMe bot ID</div><div class="set-key">.env GROUPME_BOT_ID</div></div>
      <div class="set-control"><input type="text" class="set-input" value="••••••••••••" readonly></div></div>
    <p class="muted" style="margin-top:8px">🔒 Bot tokens and IDs live in <code>.env</code>, never in the database — they are not editable from the dashboard.</p>
    <div class="form-buttons" style="justify-content:flex-end"><button class="btn btn-primary" id="plat-save">Save</button></div>`;
  document.getElementById("plat-save").onclick = () => {
    if (!gsaId) { toast("No organization found", false); return; }
    const changes = [
      { org_id: gsaId, key: "org.telegram_channel", value: val("plat-tg") },
      { org_id: gsaId, key: "org.groupme_group", value: val("plat-gm") },
    ];
    if (SERVER_URL) {
      Promise.all(changes.map((ch) => serverFetch("/settings", { method: "POST", body: ch })))
        .then(() => { toast("Platform settings applied ✅"); reloadFromServer(); })
        .catch((e) => toast("Server error: " + e.message, false));
      return;
    }
    applyAndExport(
      changes.map((ch) => settingUpdateStmt(ch.org_id, ch.key, ch.value)).join("\n"),
      "Platform settings ready to apply", { type: "settings" });
  };
}

// ───────── Tab: Judging ─────────────────────────────────────────────────────

let JUDGING_SELECTED_EVENT = null;  // currently selected event id
let JUDGING_POLL = null;            // interval id for live progress
let JUDGING_EVENTS = [];            // last-rendered events (for handlers that need name/status)

function renderJudging() {
  if (!SERVER_URL) {
    document.getElementById("tab-judging").innerHTML =
      '<p class="placeholder-msg">Judging requires server mode. Open the dashboard via <code>?server=http://localhost:5555</code>.</p>';
    return;
  }
  _judgingLoadEvents();
}

// M6: all judging POST calls require X-GSA-Dashboard header (same CSRF guard as /api/*)
function _jPost(path, body = {}) {
  return fetch(SERVER_URL + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-GSA-Dashboard": "1" },
    body: JSON.stringify(body),
  });
}

function _judgingLoadEvents() {
  fetch(SERVER_URL + "/judging/events")
    .then((r) => r.json())
    .then((events) => _judgingRender(events))
    .catch((e) => { document.getElementById("tab-judging").innerHTML = "<p>Error: " + e.message + "</p>"; });
}

function _judgingRender(events) {
  JUDGING_EVENTS = events || [];     // cache for handlers (delete needs name/status)
  const tab = document.getElementById("tab-judging");
  const sel = JUDGING_SELECTED_EVENT;

  const evRows = events.map((ev) => `
    <tr>
      <td>${ev.id}</td>
      <td><strong>${ev.name}</strong></td>
      <td><span class="badge badge-${ev.status}">${ev.status}</span></td>
      <td>Top ${ev.top_n}</td>
      <td>${ev.created_at}</td>
      <td>
        ${ev.status === "setup"  ? `<button class="btn-sm" onclick="_judgingOpen(${ev.id})">Open</button>` : ""}
        ${ev.status === "closed" ? `<button class="btn-sm" onclick="_judgingReopen(${ev.id})">Re-open</button>` : ""}
        ${ev.status === "open"   ? `<button class="btn-sm btn-danger" onclick="_judgingClose(${ev.id})">Close</button>` : ""}
        <button class="btn-sm" onclick="_judgingSelect(${ev.id})">Manage</button>
        <button class="btn-sm btn-danger" onclick="_judgingDeleteEvent(${ev.id})">Delete</button>
      </td>
    </tr>`).join("");

  tab.innerHTML = `
    <div style="padding:16px">

      <h2>Events</h2>
      ${events.length ? `<table class="data-table"><thead><tr>
        <th>ID</th><th>Name</th><th>Status</th><th>Winners</th><th>Created</th><th>Actions</th>
      </tr></thead><tbody>${evRows}</tbody></table>` : "<p>No events yet.</p>"}

      <h3 style="margin-top:24px">Create New Event</h3>
      <div style="display:grid;gap:8px;max-width:480px">
        <input id="j-ev-name" placeholder="Event name (e.g. 3MRP 2026)" style="padding:6px">
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <label style="font-size:12px">Top N winners<br>
            <input id="j-ev-topn" type="number" value="3" min="1" style="padding:6px;width:100px">
          </label>
          <label style="font-size:12px">Score min<br>
            <input id="j-ev-scoremin" type="number" value="1" min="1" style="padding:6px;width:80px">
          </label>
          <label style="font-size:12px">Score max<br>
            <input id="j-ev-scoremax" type="number" value="5" min="2" style="padding:6px;width:80px">
          </label>
          <label style="font-size:12px">Min judge coverage<br>
            <input id="j-ev-mincov" type="number" value="3" min="1" style="padding:6px;width:100px">
          </label>
          <label style="font-size:12px">Audience top N<br>
            <input id="j-ev-audtopn" type="number" value="1" min="1" style="padding:6px;width:100px">
          </label>
        </div>
        <label>Scoring criteria (one per line):</label>
        <textarea id="j-ev-criteria" rows="7" style="padding:6px;font-size:13px">${jdb_DEFAULT_CRITERIA().join("\n")}</textarea>
        <button class="btn-primary" onclick="_judgingCreate()">Create Event</button>
      </div>

      ${sel ? _judgingManageSection(sel) : ""}
    </div>`;

  // Restart live-progress poll if an open event is selected
  if (JUDGING_POLL) { clearInterval(JUDGING_POLL); JUDGING_POLL = null; }
  if (sel) {
    const ev = events.find((e) => e.id === sel);
    if (ev && ev.status === "open") {
      JUDGING_POLL = setInterval(() => _judgingRefreshProgress(sel), 10000);
      _judgingRefreshProgress(sel);
    } else {
      _judgingRefreshProgress(sel);
    }
  }
}

function jdb_DEFAULT_CRITERIA() {
  return [
    "Communication & Clarity",
    "Research Content",
    "Delivery & Engagement",
    "Organization & Timing",
    "Visual Slide Effectiveness",
    "Overall Impression",
  ];
}

function _judgingManageSection(eventId) {
  return `
    <hr style="margin:24px 0">
    <h2>Managing Event #${eventId}</h2>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:12px">

      <div>
        <h3>Load Presenters (CSV)</h3>
        <p style="font-size:12px;color:#666">Format: number,name,department (header optional)</p>
        <textarea id="j-csv" rows="6" style="width:100%;padding:6px;font-size:12px"
          placeholder="100,Jane Smith,Computer Science&#10;101,Ali Hassan,EE"></textarea>
        <button class="btn-primary" style="margin-top:6px" onclick="_judgingLoadCSV(${eventId})">Load Presenters</button>
        <div id="j-csv-result" style="margin-top:6px;font-size:13px"></div>

        <h3 style="margin-top:20px">Presenters</h3>
        <div id="j-presenters-list">Loading…</div>
      </div>

      <div>
        <h3>Add Judge</h3>
        <div style="display:grid;gap:6px;max-width:320px">
          <input id="j-judge-name" placeholder="Judge name" style="padding:6px">
          <input id="j-judge-pin" placeholder="PIN — at least 6 characters, unique" minlength="6" style="padding:6px">
          <div class="muted" style="font-size:0.8em">PIN must be at least 6 characters and unique within this event. It's stored hashed — note it down now, it can't be shown later.</div>
          <button class="btn-primary" onclick="_judgingAddJudge(${eventId})">Add Judge</button>
        </div>
        <div id="j-judges-list" style="margin-top:12px">Loading…</div>
      </div>
    </div>

    <hr style="margin:24px 0">
    <h3>Live Progress</h3>
    <div id="j-progress">Loading…</div>

    <h3 style="margin-top:20px">Results / Leaderboard</h3>
    <button class="btn-sm" onclick="_judgingLoadResults(${eventId})">Refresh Results</button>
    <button class="btn-sm" onclick="_judgingExport(${eventId})" style="margin-left:8px">Export CSV</button>
    <div id="j-results" style="margin-top:8px">Click Refresh Results to load.</div>

    <hr style="margin:24px 0">
    <h3>Audience Voting</h3>
    <div id="j-audience-ctrl">Loading…</div>

    <hr style="margin:24px 0">
    <h3>Admin: Enter / Edit a Score</h3>
    <p style="font-size:12px;color:#666">For a judge who can't use a device, or to correct a mistake.
      Overwrites the judge's current score and is recorded in the history below. Works even after the event is closed.</p>
    <div style="display:grid;gap:8px;max-width:440px">
      <select id="j-set-judge" style="padding:6px"><option value="">Select judge…</option></select>
      <input id="j-set-pnum" type="number" placeholder="Participant #" style="padding:6px">
      <div id="j-set-criteria" class="muted" style="font-size:13px">Loading criteria…</div>
      <button class="btn-primary" onclick="_judgingSetScore(${eventId})">Save score</button>
    </div>
    <div id="j-set-result" style="margin-top:6px;font-size:13px"></div>

    <h3 style="margin-top:20px">Admin: Delete a Score</h3>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <select id="j-del-judge" style="padding:6px">
        <option value="">Select judge…</option>
      </select>
      <input id="j-del-pnum" type="number" placeholder="Participant #" style="padding:6px;width:140px">
      <button class="btn-sm btn-danger" onclick="_judgingDeleteScore(${eventId})">Delete Score</button>
    </div>
    <div id="j-del-result" style="margin-top:6px;font-size:13px"></div>

    <hr style="margin:24px 0">
    <h3>Score Change History (audit)</h3>
    <p style="font-size:12px;color:#666">Every submit, admin entry, edit, and delete — newest first.</p>
    <button class="btn-sm" onclick="_judgingLoadAudit(${eventId})">Show history</button>
    <div id="j-audit" style="margin-top:8px"></div>`;
}

function _judgingSelect(eventId) {
  JUDGING_SELECTED_EVENT = eventId;
  _judgingLoadEvents();
}

function _judgingCreate() {
  const name = document.getElementById("j-ev-name").value.trim();
  const topN = parseInt(document.getElementById("j-ev-topn").value, 10) || 3;
  const scoreMin = parseInt(document.getElementById("j-ev-scoremin").value, 10) || 1;
  const scoreMax = parseInt(document.getElementById("j-ev-scoremax").value, 10) || 5;
  const minCov = parseInt(document.getElementById("j-ev-mincov").value, 10) || 3;
  const audTopN = parseInt(document.getElementById("j-ev-audtopn").value, 10) || 1;
  const criteriaRaw = document.getElementById("j-ev-criteria").value;
  const criteria = criteriaRaw.split("\n").map((l) => l.trim()).filter(Boolean);
  if (!name) { toast("Event name required", false); return; }
  if (scoreMin >= scoreMax) { toast("Score min must be less than score max", false); return; }
  _jPost("/judging/events", { name, top_n: topN, criteria, score_min: scoreMin, score_max: scoreMax, min_coverage: minCov, audience_top_n: audTopN })
  .then((r) => r.json()).then((d) => {
    toast("Event created: " + d.name);
    JUDGING_SELECTED_EVENT = d.id;
    _judgingLoadEvents();
  }).catch((e) => toast("Error: " + e.message, false));
}

function _judgingOpen(eventId, reopen) {
  _jPost(`/judging/events/${eventId}/open`)
    .then((r) => r.json().catch(() => ({})).then((d) => {
      if (d && d.error) { toast("Error: " + d.error, false); return; }  // e.g. another event already open
      toast(reopen ? "Judging re-opened" : "Judging opened");
      _judgingLoadEvents();
    }))
    .catch((e) => toast("Error: " + e.message, false));
}

function _judgingReopen(eventId) {
  if (!confirm("Re-open this closed event? Judges will be able to submit scores again. "
             + "Only one event can be open at a time.")) return;
  _judgingOpen(eventId, true);
}

function _judgingDeleteEvent(eventId) {
  const ev = JUDGING_EVENTS.find((e) => e.id === eventId) || {};
  const name = ev.name || ("#" + eventId);
  const warn = ev.status === "open" ? "\n\n⚠️ This event is currently OPEN." : "";
  if (!confirm(`Permanently DELETE event "${name}" (#${eventId}) and ALL its data — `
             + `judges, presenters, scores, audience votes, and score history?`
             + `\n\nThis CANNOT be undone.${warn}`)) return;
  _jPost(`/judging/events/${eventId}/delete-event`)
    .then((r) => r.json().catch(() => ({})).then((d) => {
      if (d && d.error) { toast("Error: " + d.error, false); return; }
      if (JUDGING_SELECTED_EVENT === eventId) JUDGING_SELECTED_EVENT = null;  // clear Manage view
      toast("Event deleted");
      _judgingLoadEvents();
    }))
    .catch((e) => toast("Error: " + e.message, false));
}

function _judgingClose(eventId) {
  if (!confirm("Close judging? Judges will no longer be able to submit scores.")) return;
  _jPost(`/judging/events/${eventId}/close`)
    .then(() => { toast("Judging closed");
      if (JUDGING_POLL) { clearInterval(JUDGING_POLL); JUDGING_POLL = null; }
      _judgingLoadEvents(); })
    .catch((e) => toast("Error: " + e.message, false));
}

function _judgingLoadCSV(eventId) {
  const csv = document.getElementById("j-csv").value;
  if (!csv.trim()) { toast("Paste CSV first", false); return; }
  _jPost(`/judging/events/${eventId}/presenters`, { csv })
  .then((r) => r.json()).then((d) => {
    document.getElementById("j-csv-result").textContent = `Loaded ${d.loaded} presenter(s).`;
    _judgingRefreshProgress(eventId);
  }).catch((e) => toast("Error: " + e.message, false));
}

function _judgingAddJudge(eventId) {
  const name = document.getElementById("j-judge-name").value.trim();
  const pin  = document.getElementById("j-judge-pin").value.trim();
  if (!name || !pin) { toast("Name and PIN required", false); return; }
  if (pin.length < 6) { toast("PIN must be at least 6 characters", false); return; }
  _jPost(`/judging/events/${eventId}/judges`, { name, pin })
  .then((r) => r.json()).then(() => {
    document.getElementById("j-judge-name").value = "";
    document.getElementById("j-judge-pin").value = "";
    toast("Judge added");
    _judgingRefreshProgress(eventId);
  }).catch((e) => toast("Error: " + e.message, false));
}

function _judgingRefreshProgress(eventId) {
  fetch(SERVER_URL + `/judging/events/${eventId}/status`)
    .then((r) => r.json())
    .then((d) => {
      const p = d.progress;
      const prog = document.getElementById("j-progress");
      if (prog) prog.innerHTML = `
        <table class="data-table" style="max-width:400px">
          <tr><td>Judges authenticated</td><td><strong>${p.authenticated_judges} / ${p.total_judges}</strong></td></tr>
          <tr><td>Presenters loaded</td><td><strong>${p.total_presenters}</strong></td></tr>
          <tr><td>Scores submitted</td><td><strong>${p.scores_submitted} / ${p.max_possible}</strong></td></tr>
          <tr><td>Coverage</td><td><strong>${p.coverage_pct}%</strong></td></tr>
        </table>`;

      // Judges table
      const jlist = document.getElementById("j-judges-list");
      const delSel = document.getElementById("j-del-judge");
      if (jlist && d.judges) {
        jlist.innerHTML = `<table class="data-table"><thead><tr>
          <th>Name</th><th>PIN</th><th>Auth'd</th><th></th>
        </tr></thead><tbody>` +
          d.judges.map((j) => `<tr>
            <td>${j.name}</td>
            <td><code title="PIN is hashed — distribute privately at setup time">set ✓</code></td>
            <td>${j.authenticated ? "✅" : "—"}</td>
            <td><button class="btn-sm btn-danger" onclick="_judgingDeleteJudge(${eventId},${j.id})">Remove</button></td>
          </tr>`).join("") +
        "</tbody></table>";
        const judgeOpts = '<option value="">Select judge…</option>' +
          d.judges.map((j) => `<option value="${j.id}">${esc(j.name)}</option>`).join("");
        // Preserve any in-progress selection across the 10s poll re-render.
        [delSel, document.getElementById("j-set-judge")].forEach((sel) => {
          if (!sel) return;
          const prev = sel.value;
          sel.innerHTML = judgeOpts;
          if (prev) sel.value = prev;
        });
      }

      // Admin enter/edit score: one input per criterion. Only (re)build when the criteria/
      // range actually change — NOT on every poll — so typed-in scores aren't wiped.
      const critBox = document.getElementById("j-set-criteria");
      if (critBox && d.event && Array.isArray(d.event.criteria)) {
        const lo = d.event.score_min, hi = d.event.score_max;
        const sig = `${lo}-${hi}:${d.event.criteria.join("|")}`;
        if (critBox.dataset.sig !== sig) {
          critBox.dataset.min = lo; critBox.dataset.max = hi; critBox.dataset.sig = sig;
          critBox.innerHTML = d.event.criteria.map((c, i) =>
            `<label style="display:flex;justify-content:space-between;gap:8px;align-items:center;margin:2px 0">
               <span>${esc(c)}</span>
               <input class="j-set-score" data-idx="${i}" type="number" min="${lo}" max="${hi}"
                      style="padding:4px;width:80px" placeholder="${lo}–${hi}">
             </label>`).join("");
        }
      }

      // Presenters table
      fetch(SERVER_URL + `/judging/events/${eventId}/presenters`)
        .then((r) => r.json())
        .then((ps) => {
          const plist = document.getElementById("j-presenters-list");
          if (!plist) return;
          if (!ps.length) { plist.innerHTML = "<p>No presenters loaded yet.</p>"; return; }
          plist.innerHTML = `<table class="data-table"><thead><tr>
            <th>#</th><th>Name</th><th>Department</th><th>Present</th><th>Scores</th>
          </tr></thead><tbody>` +
            ps.map((p) => `<tr>
              <td>${p.number}</td>
              <td>${p.name}</td>
              <td>${p.department || "—"}</td>
              <td>${p.is_present
                ? (p.has_telegram ? "✅ (Telegram)" : "✅ (Manual)")
                : `<button class="btn-sm" onclick="_judgingMarkPresent(${eventId},${p.number})">Mark Present</button>`}
              </td>
              <td><button class="btn-sm" onclick="_judgingPresenterScores(${eventId},${p.number},'${p.name.replace(/'/g,"\\'")}')">View</button></td>
            </tr>`).join("") +
          "</tbody></table>";
        });
    })
    .catch(() => {});

  _judgingRefreshAudience(eventId);
}

function _judgingRefreshAudience(eventId) {
  fetch(SERVER_URL + `/judging/events/${eventId}/audience-results`)
    .then((r) => r.json())
    .then((d) => {
      const ctrl = document.getElementById("j-audience-ctrl");
      if (!ctrl) return;
      const ev = d.event;
      const isOpen = ev && ev.audience_voting === "open";
      const topN = ev ? ev.audience_top_n : 1;
      const totalVotes = d.results ? d.results.reduce((s, r) => s + r.vote_count, 0) : 0;

      const openBtn = isOpen
        ? `<button class="btn-sm btn-danger" onclick="_judgingAudienceClose(${eventId})">Close Audience Voting</button>`
        : `<button class="btn-sm btn-primary" onclick="_judgingAudienceOpen(${eventId})">Open Audience Voting</button>`;

      let resultsHtml = "<p>No votes yet.</p>";
      if (d.results && d.results.some((r) => r.vote_count > 0)) {
        const winners = d.results.filter((r) => r.rank !== null && r.rank <= topN);
        resultsHtml = `
          <p style="font-size:12px;color:#666">Top ${topN} audience winner(s) · ${totalVotes} total vote(s)</p>
          <table class="data-table"><thead><tr>
            <th>Rank</th><th>#</th><th>Name</th><th>Department</th><th>Votes</th>
          </tr></thead><tbody>` +
          d.results.filter((r) => r.vote_count > 0).map((r) => `<tr style="${winners.some(w => w.number === r.number) ? 'font-weight:bold' : ''}">
            <td>${r.rank !== null ? r.rank : "—"}</td>
            <td>${r.number}</td>
            <td>${r.name}${winners.some(w => w.number === r.number) ? " 🏆" : ""}</td>
            <td>${r.department || "—"}</td>
            <td>${r.vote_count}</td>
          </tr>`).join("") +
          "</tbody></table>";
      }

      ctrl.innerHTML = `
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
          <span>Status: <strong>${isOpen ? "🟢 Open" : "🔴 Closed"}</strong></span>
          ${openBtn}
        </div>
        ${resultsHtml}`;
    })
    .catch(() => {});
}

function _judgingAudienceOpen(eventId) {
  _jPost(`/judging/events/${eventId}/audience-open`)
    .then(() => { toast("Audience voting opened"); _judgingRefreshAudience(eventId); })
    .catch((e) => toast("Error: " + e.message, false));
}

function _judgingAudienceClose(eventId) {
  if (!confirm("Close audience voting?")) return;
  _jPost(`/judging/events/${eventId}/audience-close`)
    .then(() => { toast("Audience voting closed"); _judgingRefreshAudience(eventId); })
    .catch((e) => toast("Error: " + e.message, false));
}

function _judgingLoadResults(eventId) {
  fetch(SERVER_URL + `/judging/events/${eventId}/results`)
    .then((r) => r.json())
    .then((d) => {
      const div = document.getElementById("j-results");
      if (!d.leaderboard.length) { div.innerHTML = "<p>No scores yet.</p>"; return; }
      const minCov = d.event ? d.event.min_coverage : 0;
      div.innerHTML = `<p style="font-size:12px;color:#666">⚠️ = fewer than ${minCov} judges (low coverage)</p>
        <table class="data-table"><thead><tr>
        <th>Rank</th><th>#</th><th>Name</th><th>Department</th>
        <th>Avg Score</th><th>Judges</th>
      </tr></thead><tbody>` +
        d.leaderboard.map((r) => `<tr style="${r.low_coverage ? 'opacity:0.65' : ''}">
          <td>${r.rank !== null ? r.rank : "—"}</td>
          <td>${r.number}</td>
          <td>${r.name}${r.low_coverage ? ' ⚠️' : ''}</td>
          <td>${r.department}</td>
          <td>${r.avg_score !== null ? r.avg_score.toFixed(3) : "—"}</td>
          <td>${r.judge_count}</td>
        </tr>`).join("") +
      "</tbody></table>";
    })
    .catch((e) => toast("Error: " + e.message, false));
}

function _judgingMarkPresent(eventId, presenterNumber) {
  _jPost(`/judging/events/${eventId}/present`, { presenter_number: presenterNumber })
    .then(() => { toast("Marked present"); _judgingRefreshProgress(eventId); })
    .catch((e) => toast("Error: " + e.message, false));
}

function _judgingPresenterScores(eventId, presenterNumber, presenterName) {
  fetch(SERVER_URL + `/judging/events/${eventId}/presenters/${presenterNumber}/scores`)
    .then((r) => r.json())
    .then((rows) => {
      const bodyEl = document.getElementById("modal-body");
      if (!rows.length) {
        bodyEl.innerHTML = `<h2>#${presenterNumber} — ${presenterName}</h2><p>No scores submitted yet.</p>
          <div class="modal-actions"><button class="btn btn-primary" onclick="closeModal()">Close</button></div>`;
        document.getElementById("modal").hidden = false;
        return;
      }
      const header = Object.keys(rows[0].scores);
      const tableRows = rows.map((r) =>
        `<tr><td>${r.judge_name}</td>` +
        header.map((c) => `<td>${r.scores[c] ?? "—"}</td>`).join("") +
        `<td><strong>${r.final_score.toFixed(2)}</strong></td><td style="font-size:11px;color:#888">${r.submitted_at || ""}</td></tr>`
      ).join("");
      bodyEl.innerHTML = `
        <h2>#${presenterNumber} — ${presenterName}</h2>
        <table class="data-table">
          <thead><tr><th>Judge</th>${header.map((c) => `<th>${c}</th>`).join("")}<th>Avg</th><th>Submitted</th></tr></thead>
          <tbody>${tableRows}</tbody>
        </table>
        <div class="modal-actions"><button class="btn btn-primary" onclick="closeModal()">Close</button></div>`;
      document.getElementById("modal").hidden = false;
    })
    .catch((e) => toast("Error: " + e.message, false));
}

function _judgingExport(eventId) {
  window.open(SERVER_URL + `/judging/events/${eventId}/export`, "_blank");
}

function _judgingDeleteScore(eventId) {
  const judgeId = document.getElementById("j-del-judge").value;
  const pnum    = document.getElementById("j-del-pnum").value;
  if (!judgeId || !pnum) { toast("Select a judge and enter participant number", false); return; }
  if (!confirm(`Delete score for participant #${pnum} by this judge?`)) return;
  _jPost(`/judging/events/${eventId}/scores-delete`, { judge_id: parseInt(judgeId), presenter_number: parseInt(pnum) })
  .then((r) => r.json()).then((d) => {
    document.getElementById("j-del-result").textContent =
      d.deleted ? "Score deleted. Judge can re-score." : "Score not found.";
    _judgingRefreshProgress(eventId);
  }).catch((e) => toast("Error: " + e.message, false));
}

function _judgingDeleteJudge(eventId, judgeId) {
  if (!confirm("Remove this judge? This will fail if they have submitted scores — delete their scores first.")) return;
  _jPost(`/judging/events/${eventId}/judges-delete`, { judge_id: judgeId })
    .then((r) => r.json()).then((d) => {
      if (d.success) { toast("Judge removed"); _judgingRefreshProgress(eventId); }
      else toast("Error: " + (d.error || "unknown"), false);
    })
    .catch((e) => toast("Error: " + e.message, false));
}

function _judgingSetScore(eventId) {
  const judgeId = document.getElementById("j-set-judge").value;
  const pnum    = document.getElementById("j-set-pnum").value;
  if (!judgeId || !pnum) { toast("Select a judge and enter participant number", false); return; }
  const box = document.getElementById("j-set-criteria");
  const lo = Number(box.dataset.min), hi = Number(box.dataset.max);
  const inputs = Array.from(box.querySelectorAll(".j-set-score"))
    .sort((a, b) => Number(a.dataset.idx) - Number(b.dataset.idx));
  const scores = [];
  for (const inp of inputs) {
    if (inp.value === "") { toast("Fill in every criterion score", false); return; }
    const v = parseInt(inp.value, 10);
    if (Number.isNaN(v) || v < lo || v > hi) { toast(`Each score must be ${lo}–${hi}`, false); return; }
    scores.push(v);
  }
  _jPost(`/judging/events/${eventId}/scores-set`,
         { judge_id: parseInt(judgeId), presenter_number: parseInt(pnum), scores })
  .then((r) => r.json()).then((d) => {
    if (d.error) { toast("Error: " + d.error, false); return; }
    document.getElementById("j-set-result").textContent =
      (d.edited ? "Score updated" : "Score entered") + ` (final ${d.final_score.toFixed(2)}).`;
    inputs.forEach((i) => { i.value = ""; });
    _judgingRefreshProgress(eventId);
  }).catch((e) => toast("Error: " + e.message, false));
}

function _judgingLoadAudit(eventId) {
  fetch(SERVER_URL + `/judging/events/${eventId}/audit`)
    .then((r) => r.json()).then((d) => {
      const box = document.getElementById("j-audit");
      if (!box) return;
      const rows = (d.audit || []);
      if (!rows.length) { box.innerHTML = "<p>No score changes recorded yet.</p>"; return; }
      box.innerHTML = `<table class="data-table"><thead><tr>
        <th>When</th><th>Action</th><th>By</th><th>Judge</th><th>#</th><th>Scores</th><th>Final</th>
      </tr></thead><tbody>` +
        rows.map((a) => `<tr>
          <td>${esc(a.created_at)}</td>
          <td>${esc(a.action)}</td>
          <td>${esc(a.actor)}${a.actor_label && a.actor_label !== a.actor ? " (" + esc(a.actor_label) + ")" : ""}</td>
          <td>${esc(a.judge_name || ("#" + a.judge_id))}</td>
          <td>${a.presenter_number}</td>
          <td>${a.scores ? esc(Object.values(a.scores).join(", ")) : "—"}</td>
          <td>${a.final_score != null ? a.final_score.toFixed(2) : "—"}</td>
        </tr>`).join("") +
      "</tbody></table>";
    }).catch((e) => toast("Error: " + e.message, false));
}
