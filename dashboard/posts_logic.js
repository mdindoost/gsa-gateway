/* GSA Gateway v2 dashboard — pure post-creation logic (no DOM).
   Shared by the browser (app.js) and the Node verification harness, so the exact
   INSERT logic the dashboard runs is unit-testable with real sql.js.
   Every function takes a sql.js `db`; none touch the DOM or window. */

(function (global) {
  // ── tiny query helpers ──────────────────────────────────────────────────
  function q(db, sql, params = []) {
    const stmt = db.prepare(sql);
    if (params.length) stmt.bind(params);
    const rows = [];
    while (stmt.step()) rows.push(stmt.getAsObject());
    stmt.free();
    return rows;
  }
  function lastId(db) { return q(db, "SELECT last_insert_rowid() AS id")[0].id; }

  // ── date helpers (local wall-clock, "YYYY-MM-DD HH:MM:SS") ───────────────
  const pad = (n) => String(n).padStart(2, "0");
  const fmtDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const fmtDateTime = (d) =>
    `${fmtDate(d)} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  const nowStr = () => fmtDateTime(new Date());
  function combineDateTime(dateStr, timeStr) {
    if (!dateStr) return null;
    return `${dateStr} ${timeStr || "09:00"}:00`;
  }
  function parseHHMM(s) {
    const m = /(\d{1,2}):(\d{2})/.exec(s || "09:00");
    return m ? [parseInt(m[1], 10), parseInt(m[2], 10)] : [9, 0];
  }

  // ── settings inheritance (self → parent → root) ─────────────────────────
  function orgAncestors(db, orgId) {
    return q(db,
      "WITH RECURSIVE up(id,parent_id,depth) AS (" +
      " SELECT id,parent_id,0 FROM organizations WHERE id=?" +
      " UNION ALL SELECT o.id,o.parent_id,up.depth+1 FROM organizations o JOIN up ON o.id=up.parent_id" +
      ") SELECT id FROM up ORDER BY depth", [orgId]).map((r) => r.id);
  }
  function getSetting(db, orgId, key, def = null) {
    for (const oid of orgAncestors(db, orgId)) {
      const r = q(db, "SELECT value FROM settings WHERE org_id=? AND key=?", [oid, key]);
      if (r.length) return r[0].value;
    }
    return def;
  }
  function getSettingJSON(db, orgId, key, def) {
    const v = getSetting(db, orgId, key, null);
    if (v == null) return def;
    try { return JSON.parse(v); } catch (e) { return def; }
  }

  // ── signature rendering ─────────────────────────────────────────────────
  function renderSignature(db, orgId, template) {
    const tmpl = template == null ? (getSetting(db, orgId, "signature.default", "") || "") : template;
    if (!tmpl) return "";
    const vars = getSettingJSON(db, orgId, "signature.variables", {}) || {};
    return tmpl.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  }

  // ── recurrence math (mirrors v2 scheduler; days_of_week = Python Mon=0) ──
  function addMonths(d, n) {
    const x = new Date(d), day = x.getDate();
    x.setDate(1); x.setMonth(x.getMonth() + n);
    const last = new Date(x.getFullYear(), x.getMonth() + 1, 0).getDate();
    x.setDate(Math.min(day, last));
    return x;
  }
  const pyDow = (jsDay) => (jsDay + 6) % 7; // JS Sun=0 → Python Mon=0
  function nextOccurrence(rec, after) {
    const freq = rec.freq || "daily", interval = parseInt(rec.interval || 1, 10);
    const [hh, mm] = parseHHMM(rec.time);
    if (["once", "event_driven", "none"].includes(freq)) return null;
    let nxt;
    if (freq === "daily") {
      nxt = new Date(after); nxt.setDate(nxt.getDate() + interval); nxt.setHours(hh, mm, 0, 0);
    } else if (freq === "weekly") {
      const days = (rec.days_of_week && rec.days_of_week.length)
        ? rec.days_of_week.map(Number) : [pyDow(after.getDay())];
      nxt = null;
      for (let add = 1; add <= 7; add++) {
        const c = new Date(after); c.setDate(c.getDate() + add); c.setHours(hh, mm, 0, 0);
        if (days.includes(pyDow(c.getDay()))) { nxt = c; break; }
      }
      if (!nxt) { nxt = new Date(after); nxt.setDate(nxt.getDate() + 7); nxt.setHours(hh, mm, 0, 0); }
    } else if (freq === "monthly") {
      nxt = addMonths(after, interval); nxt.setHours(hh, mm, 0, 0);
    } else return null;
    if (rec.end && fmtDate(nxt) > rec.end) return null;
    return nxt;
  }
  function nextOccurrences(rec, after, count) {
    const out = [];
    let cur = new Date(after);
    if (rec.start) {
      const s = new Date(rec.start + "T00:00:00");
      if (s > cur) cur = new Date(s.getTime() - 1000);
    }
    for (let i = 0; i < count; i++) {
      const n = nextOccurrence(rec, cur);
      if (!n) break;
      out.push(n); cur = n;
    }
    return out;
  }
  function reminderFireTime(dateStr, timeStr, offset, unit) {
    if (!dateStr) return null;
    const [hh, mm] = parseHHMM(timeStr && timeStr !== "TBD" ? timeStr : "09:00");
    const dt = new Date(dateStr + "T00:00:00"); dt.setHours(hh, mm, 0, 0);
    const ms = { minutes: 6e4, hours: 36e5, days: 864e5, weeks: 6048e5 }[unit];
    return ms ? new Date(dt.getTime() - offset * ms) : null;
  }

  // ── dashboard prep ──────────────────────────────────────────────────────
  // The sql.js WASM build has no FTS5, so the knowledge_fts sync triggers would
  // error on ANY knowledge_items write. Drop them in this in-memory copy so the
  // dashboard can author content. After the db is saved, rebuild_index.py
  // recreates the triggers (create_all is idempotent) and rebuilds the FTS index
  // + embeds new items — which dashboard-authored content needs regardless,
  // since there is no Ollama in the browser.
  function prepareForDashboard(db) {
    for (const t of ["knowledge_items_fts_ai", "knowledge_items_fts_ad", "knowledge_items_fts_au"]) {
      try { db.run("DROP TRIGGER IF EXISTS " + t); } catch (e) { /* ignore */ }
    }
  }

  // ── creators ────────────────────────────────────────────────────────────
  function createOneTimePost(db, d) {
    const channels = JSON.stringify(d.platforms || []);
    const scheduled = d.sendImmediately ? nowStr() : combineDateTime(d.date, d.time);
    db.run(
      "INSERT INTO posts(org_id,type,title,content,channels,discord_channel,scheduled_for," +
      "status,source_type,signature,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
      [d.orgId, d.postType || "one_time", d.title || null, d.content, channels,
       d.discordChannel || null, scheduled, "scheduled", "manual",
       d.signature == null ? null : d.signature, "dashboard", nowStr()]);
    return lastId(db);
  }

  function createRecurringTemplate(db, d) {
    const channels = JSON.stringify(d.platforms || []);
    const first = nextOccurrences(d.recurrence, new Date(), 1)[0];
    db.run(
      "INSERT INTO post_templates(org_id,name,content,post_type,recurrence,channels," +
      "discord_channel,signature,enabled,next_run_at,created_by,created_at) " +
      "VALUES(?,?,?,?,?,?,?,?,1,?,?,?)",
      [d.orgId, d.name || "Recurring post", d.content, d.postType || "recurring_instance",
       JSON.stringify(d.recurrence), channels, d.discordChannel || null,
       d.signature == null ? null : d.signature, first ? fmtDateTime(first) : null,
       "dashboard", nowStr()]);
    return lastId(db);
  }

  function createEvent(db, d) {
    const now = nowStr();
    db.run(
      "INSERT INTO events(org_id,name,date,time,location,description,organizer,category," +
      "created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
      [d.orgId, d.name, d.date, d.time || "TBD", d.location || "TBD", d.description || "",
       "GSA", "general", now, "dashboard"]);
    const eventId = lastId(db);

    const kiContent = `${d.name} — ${d.date} at ${d.time || "TBD"}, ${d.location || "TBD"}.` +
      (d.description ? "\n" + d.description : "");
    db.run(
      "INSERT INTO knowledge_items(org_id,type,title,content,metadata,created_by) VALUES(?,?,?,?,?,?)",
      [d.orgId, "event_info", d.name, kiContent,
       JSON.stringify({ event_id: eventId, date: d.date, time: d.time, location: d.location }),
       "dashboard"]);

    const channels = JSON.stringify(d.platforms || []);
    const annContent = d.announceContent ||
      `📅 ${d.name}\n${d.date} at ${d.time || "TBD"} · ${d.location || ""}\n${d.description || ""}`.trim();
    const annSched = d.announceImmediately ? now
      : combineDateTime(d.announceDate || d.date, d.announceTime || "09:00");
    db.run(
      "INSERT INTO posts(org_id,type,title,content,channels,discord_channel,scheduled_for," +
      "status,source_type,source_id,signature,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
      [d.orgId, "event_announcement", d.name, annContent, channels, d.discordChannel || null,
       annSched, "scheduled", "event", eventId, d.signature == null ? null : d.signature,
       "dashboard", now]);
    const announcementId = lastId(db);

    const reminderPostIds = [];
    for (const r of (d.reminders || [])) {
      if (!r.enabled) continue;
      const rchannels = JSON.stringify(r.platforms || d.platforms || []);
      db.run(
        "INSERT INTO event_reminders(event_id,offset_value,offset_unit,channels,enabled,created_at) " +
        "VALUES(?,?,?,?,1,?)", [eventId, r.offset, r.unit, rchannels, now]);
      const remId = lastId(db);
      const fire = reminderFireTime(d.date, d.time, r.offset, r.unit);
      const fireStr = fire ? fmtDateTime(fire) : annSched;
      const rContent = `⏰ Reminder: ${d.name} is on ${d.date} at ${d.time || "TBD"}, ${d.location || ""}.`;
      db.run(
        "INSERT INTO posts(org_id,type,title,content,channels,scheduled_for,status," +
        "source_type,source_id,signature,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        [d.orgId, "event_reminder", `Reminder: ${d.name}`, rContent, rchannels, fireStr,
         "scheduled", "event_reminder", eventId, d.signature == null ? null : d.signature,
         "dashboard", now]);
      const rpid = lastId(db);
      db.run("UPDATE event_reminders SET post_id=? WHERE id=?", [rpid, remId]);
      reminderPostIds.push(rpid);
    }
    return { eventId, announcementId, reminderPostIds };
  }

  // ── knowledge base + organizations ──────────────────────────────────────
  function slugify(name) {
    return (name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "").slice(0, 40) || "node";
  }
  function addOrganization(db, d) {
    let slug = slugify(d.name), base = slug, i = 2;
    while (q(db, "SELECT 1 FROM organizations WHERE parent_id IS ? AND slug=?", [d.parentId || null, slug]).length) {
      slug = base + "-" + (i++);
    }
    db.run(
      "INSERT INTO organizations(parent_id,name,slug,type,description,metadata) VALUES(?,?,?,?,?,?)",
      [d.parentId || null, d.name, slug, d.type || "custom", d.description || null, JSON.stringify(d.metadata || {})]);
    return lastId(db);
  }

  function createKnowledgeItem(db, d) {
    db.run(
      "INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,created_by) VALUES(?,?,?,?,?,?,?)",
      [d.orgId, d.type || "faq", d.title || null, d.content, JSON.stringify(d.metadata || {}),
       d.sourceUrl || null, "dashboard"]);
    return lastId(db); // version=1, root_id set by trigger
  }

  function updateKnowledgeItem(db, d) {
    const old = q(db, "SELECT * FROM knowledge_items WHERE id=?", [d.id])[0];
    if (!old) return null;
    const pick = (k, col) => (d[k] !== undefined ? d[k] : old[col]);
    db.run(
      "INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,version,root_id,parent_id,created_by) " +
      "VALUES(?,?,?,?,?,?,?,?,?,?)",
      [old.org_id, pick("type", "type"), pick("title", "title"), pick("content", "content"),
       JSON.stringify(d.metadata || (old.metadata ? JSON.parse(old.metadata) : {})),
       pick("sourceUrl", "source_url"), (old.version || 1) + 1, old.root_id || old.id, old.id, "dashboard"]);
    const newId = lastId(db);
    db.run("UPDATE knowledge_items SET is_active=0, updated_at=? WHERE id=?", [nowStr(), old.id]);
    return newId;
  }

  function restoreVersion(db, versionId) {
    const old = q(db, "SELECT * FROM knowledge_items WHERE id=?", [versionId])[0];
    if (!old) return null;
    const cur = q(db, "SELECT * FROM knowledge_items WHERE root_id=? AND is_active=1", [old.root_id || old.id])[0];
    return updateKnowledgeItem(db, {
      id: cur.id, type: old.type, title: old.title, content: old.content,
      metadata: old.metadata ? JSON.parse(old.metadata) : {}, sourceUrl: old.source_url,
    });
  }

  function deactivateKnowledgeItem(db, id) {
    db.run("UPDATE knowledge_items SET is_active=0, updated_at=? WHERE id=?", [nowStr(), id]);
  }

  function updateSetting(db, orgId, key, value) {
    db.run("UPDATE settings SET value=?, updated_at=?, updated_by='dashboard' WHERE org_id=? AND key=?",
      [value, nowStr(), orgId, key]);
  }

  function missingVectorCount(db) {
    try {
      return q(db, "SELECT COUNT(*) c FROM knowledge_items WHERE is_active=1 " +
        "AND id NOT IN (SELECT rowid FROM knowledge_vectors_rowids)")[0].c;
    } catch (e) { return 0; }
  }

  // ── timezone (Fix 3): store UTC, show org-local ──────────────────────────
  function orgTimezone(db) {
    try {
      const r = q(db, "SELECT value FROM settings WHERE key='org.timezone' AND value IS NOT NULL ORDER BY org_id LIMIT 1");
      return (r[0] && r[0].value) || "America/New_York";
    } catch (e) { return "America/New_York"; }
  }
  function nowUTC() {
    const d = new Date();
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
           `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
  }
  function _tzOffsetMs(date, tz) {
    const dtf = new Intl.DateTimeFormat("en-US", {
      timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit",
      day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
    const p = {};
    dtf.formatToParts(date).forEach((x) => { if (x.type !== "literal") p[x.type] = x.value; });
    const asTz = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second);
    return asTz - date.getTime();
  }
  // "YYYY-MM-DD HH:MM[:SS]" interpreted as wall-clock in tz → UTC string
  function localToUTC(localStr, tz) {
    if (!localStr) return null;
    const s = localStr.length === 16 ? localStr + ":00" : localStr;
    const guess = new Date(s.replace(" ", "T") + "Z");
    const utc = new Date(guess.getTime() - _tzOffsetMs(guess, tz));
    return `${utc.getUTCFullYear()}-${pad(utc.getUTCMonth() + 1)}-${pad(utc.getUTCDate())} ` +
           `${pad(utc.getUTCHours())}:${pad(utc.getUTCMinutes())}:${pad(utc.getUTCSeconds())}`;
  }
  // UTC db string → friendly org-local display ("Jun 12, 6:00 PM EDT")
  function utcToLocal(utcStr, tz, abbrev = false) {
    if (!utcStr) return "—";
    const iso = utcStr.includes("T") ? utcStr : utcStr.replace(" ", "T");
    const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
    if (isNaN(d)) return utcStr;
    const opt = { timeZone: tz, month: "short", day: "numeric", hour: "numeric", minute: "2-digit", hour12: true };
    if (abbrev) opt.timeZoneName = "short";
    return new Intl.DateTimeFormat("en-US", opt).format(d);
  }

  // ── SQL patch builders (Fix 2): never write live db from the browser ──────
  function sqlLit(v) {
    if (v === null || v === undefined) return "NULL";
    if (typeof v === "number") return String(v);
    return "'" + String(v).replace(/'/g, "''") + "'";
  }
  function _row(cols, vals) {
    return `(${cols.join(",")})\n  VALUES (${vals.map(sqlLit).join(", ")})`;
  }
  function _header(typeLabel, summary, tz) {
    let h = `-- GSA Gateway v2 change patch\n-- Generated: ${utcToLocal(nowUTC(), tz, true)}\n-- Type: ${typeLabel}\n`;
    (summary || []).forEach((s) => { h += `-- ${s}\n`; });
    return h + "\n";
  }

  // d datetimes are already UTC. Returns a BEGIN…COMMIT patch string.
  function buildPostPatch(d, tz) {
    const summary = [
      `Creating: ${JSON.stringify((d.title || d.content || "").slice(0, 60))}`,
      `Scheduled: ${d.scheduledForUTC ? utcToLocal(d.scheduledForUTC, tz, true) + " (= " + d.scheduledForUTC + " UTC)" : "immediately"}`,
      `Platforms: ${(d.platforms || []).join(", ")}`,
    ];
    const cols = ["org_id", "type", "title", "content", "channels", "discord_channel",
      "scheduled_for", "status", "source_type", "signature", "created_by", "created_at"];
    const vals = [d.orgId, d.type || "one_time", d.title || null, d.content,
      JSON.stringify(d.platforms || []), d.discordChannel || null, d.scheduledForUTC || null,
      "scheduled", d.sourceType || "manual", d.signature == null ? null : d.signature,
      "dashboard", nowUTC()];
    return _header("post (one-time)", summary, tz) +
      `BEGIN TRANSACTION;\nINSERT INTO posts${_row(cols, vals)};\nCOMMIT;\n`;
  }

  function buildRecurringPatch(d, tz) {
    const cols = ["org_id", "name", "content", "post_type", "recurrence", "channels",
      "discord_channel", "signature", "enabled", "next_run_at", "created_by", "created_at"];
    const vals = [d.orgId, d.name || "Recurring post", d.content, d.postType || "recurring_instance",
      JSON.stringify(d.recurrence), JSON.stringify(d.platforms || []), d.discordChannel || null,
      d.signature == null ? null : d.signature, 1, d.nextRunUTC || null, "dashboard", nowUTC()];
    return _header("recurring template", [`Repeat: ${JSON.stringify(d.recurrence)}`], tz) +
      `BEGIN TRANSACTION;\nINSERT INTO post_templates${_row(cols, vals)};\nCOMMIT;\n`;
  }

  function buildEventPatch(d, tz) {
    const ts = nowUTC();
    const evSel = `(SELECT id FROM events WHERE created_at=${sqlLit(ts)} AND name=${sqlLit(d.name)})`;
    const summary = [
      `Event: ${JSON.stringify(d.name)}`,
      `Starts: ${utcToLocal(d.startUTC, tz, true)} (= ${d.startUTC} UTC)`,
      `Reminders: ${(d.reminders || []).filter((r) => r.enabled).map((r) => r.offset + r.unit[0]).join(", ") || "none"}`,
    ];
    let sql = _header("event (+announcement +reminders)", summary, tz) + "BEGIN TRANSACTION;\n";
    sql += "INSERT INTO events" + _row(
      ["org_id", "name", "date", "time", "location", "description", "organizer", "category", "created_at", "created_by"],
      [d.orgId, d.name, d.dateUTC, d.timeUTC || "TBD", d.location || "TBD", d.description || "", "GSA", "general", ts, "dashboard"]) + ";\n";
    // events table stores UTC date/time (for reminder math); the knowledge item
    // shown to readers uses the admin's local display values.
    const ki = d.kiContent ||
      `${d.name} — ${d.dateUTC} at ${d.timeUTC || "TBD"}, ${d.location || "TBD"}.` + (d.description ? "\n" + d.description : "");
    sql += "INSERT INTO knowledge_items" + _row(
      ["org_id", "type", "title", "content", "metadata", "created_by"],
      [d.orgId, "event_info", d.name, ki, JSON.stringify({ date: d.dateUTC, time: d.timeUTC, location: d.location }), "dashboard"]) + ";\n";
    sql += "INSERT INTO posts(org_id,type,title,content,channels,discord_channel,scheduled_for,status,source_type,source_id,signature,created_by,created_at)\n  VALUES (" +
      [d.orgId, "event_announcement", d.name, d.announceContent, JSON.stringify(d.platforms || []), d.discordChannel || null, d.announceUTC, "scheduled", "event"].map(sqlLit).join(", ") +
      `, ${evSel}, ${sqlLit(d.signature == null ? null : d.signature)}, 'dashboard', ${sqlLit(ts)});\n`;
    for (const r of (d.reminders || [])) {
      if (!r.enabled) continue;
      sql += "INSERT INTO event_reminders(event_id,offset_value,offset_unit,channels,enabled,created_at)\n  VALUES (" +
        `${evSel}, ${sqlLit(r.offset)}, ${sqlLit(r.unit)}, ${sqlLit(JSON.stringify(r.platforms || d.platforms || []))}, 1, ${sqlLit(ts)});\n`;
    }
    return sql + "COMMIT;\n";
  }

  function buildKnowledgeItemPatch(d, tz) {
    if (d.editOld) {
      const o = d.editOld;
      const v = (o.version || 1) + 1;
      let sql = _header(`knowledge_item (edit → v${v})`, [`Item: ${JSON.stringify(d.title || "")}`], tz) + "BEGIN TRANSACTION;\n";
      sql += "INSERT INTO knowledge_items" + _row(
        ["org_id", "type", "title", "content", "metadata", "source_url", "version", "root_id", "parent_id", "created_by"],
        [o.org_id, d.type, d.title, d.content, JSON.stringify(d.metadata || {}), d.sourceUrl || null, v, o.root_id || o.id, o.id, "dashboard"]) + ";\n";
      sql += `UPDATE knowledge_items SET is_active=0, updated_at=${sqlLit(nowUTC())} WHERE id=${sqlLit(o.id)};\n`;
      return sql + "COMMIT;\n";
    }
    return _header("knowledge_item (new)", [`Item: ${JSON.stringify(d.title || "")}`, `Org: ${d.orgId}`, `Type: ${d.type}`], tz) +
      "BEGIN TRANSACTION;\nINSERT INTO knowledge_items" + _row(
        ["org_id", "type", "title", "content", "metadata", "source_url", "created_by"],
        [d.orgId, d.type || "faq", d.title || null, d.content, JSON.stringify(d.metadata || {}), d.sourceUrl || null, "dashboard"]) + ";\nCOMMIT;\n";
  }

  function buildOrgPatch(d, tz) {
    return _header("organization", [`Add: ${JSON.stringify(d.name)} (${d.type})`], tz) +
      "BEGIN TRANSACTION;\nINSERT INTO organizations" + _row(
        ["parent_id", "name", "slug", "type", "description", "metadata"],
        [d.parentId || null, d.name, d.slug, d.type || "custom", d.description || null, JSON.stringify(d.metadata || {})]) + ";\nCOMMIT;\n";
  }

  const api = {
    q, lastId, fmtDate, fmtDateTime, nowStr, combineDateTime, parseHHMM,
    orgTimezone, nowUTC, localToUTC, utcToLocal, sqlLit,
    buildPostPatch, buildRecurringPatch, buildEventPatch, buildKnowledgeItemPatch, buildOrgPatch,
    orgAncestors, getSetting, getSettingJSON, renderSignature,
    nextOccurrence, nextOccurrences, reminderFireTime, prepareForDashboard,
    createOneTimePost, createRecurringTemplate, createEvent,
    slugify, addOrganization, createKnowledgeItem, updateKnowledgeItem,
    restoreVersion, deactivateKnowledgeItem, missingVectorCount, updateSetting,
  };
  global.PostsLogic = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
