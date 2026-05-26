/* GSA Gateway — frontend JavaScript
   Loads events from data/events.json and powers the mobile nav. */

"use strict";

// ── Mobile nav toggle ────────────────────────────────────────────────────────
(function () {
  const burger = document.querySelector(".nav-burger");
  const navLinks = document.querySelector(".nav-links");
  if (!burger || !navLinks) return;

  burger.addEventListener("click", () => {
    const open = navLinks.classList.toggle("open");
    burger.setAttribute("aria-expanded", String(open));
  });

  // Close menu on link click (mobile UX)
  navLinks.querySelectorAll("a").forEach((a) =>
    a.addEventListener("click", () => navLinks.classList.remove("open"))
  );
})();

// ── Active nav link highlight ────────────────────────────────────────────────
(function () {
  const path = location.pathname.split("/").pop() || "index.html";
  document.querySelectorAll(".nav-links a").forEach((a) => {
    const href = a.getAttribute("href") || "";
    if (href === path || (path === "" && href === "index.html")) {
      a.classList.add("active");
    }
  });
})();

// ── Events page loader ───────────────────────────────────────────────────────
const eventsContainer = document.getElementById("events-container");

if (eventsContainer) {
  loadEvents();
}

async function loadEvents() {
  eventsContainer.innerHTML = `
    <div class="loading">
      <div class="spinner"></div>
      <p>Loading events…</p>
    </div>`;

  try {
    const res = await fetch("data/events.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderEvents(data.events || []);
  } catch (err) {
    eventsContainer.innerHTML = `
      <div class="info-box">
        <strong>Events temporarily unavailable.</strong><br>
        Use <code>/events</code> in the GSA Discord server for the latest listings,
        or check back here soon.
      </div>`;
    console.warn("Events load error:", err);
  }
}

function renderEvents(events) {
  const today = new Date().toISOString().slice(0, 10);
  const upcoming = events
    .filter((e) => e.date >= today)
    .sort((a, b) => a.date.localeCompare(b.date));
  const past = events
    .filter((e) => e.date < today)
    .sort((a, b) => b.date.localeCompare(a.date));

  if (!upcoming.length && !past.length) {
    eventsContainer.innerHTML = `
      <p style="color:var(--gray-mid); text-align:center; padding:3rem 0;">
        No events right now — check back soon!
      </p>`;
    return;
  }

  function cardHtml(ev) {
    return `
    <div class="card">
      <span class="card-tag">${escHtml(ev.category || "event")}</span>
      <h3>${escHtml(ev.name)}</h3>
      <p>${escHtml(ev.description || "")}</p>
      <div class="card-meta">
        <span>📅 ${escHtml(ev.date)}</span>
        <span>🕐 ${escHtml(ev.time)}</span>
        <span>📍 ${escHtml(ev.location)}</span>
      </div>
      ${
        ev.rsvp_link
          ? `<a href="${escHtml(ev.rsvp_link)}" class="btn btn-primary"
               style="margin-top:1rem;font-size:.85rem;" target="_blank" rel="noopener">
               RSVP →
             </a>`
          : ""
      }
    </div>`;
  }

  let html = "";

  if (upcoming.length) {
    html += `<div class="card-grid">${upcoming.map(cardHtml).join("")}</div>`;
  } else {
    html += `<p style="color:var(--gray-mid); text-align:center; padding:1.5rem 0;">
      No upcoming events right now — check back soon!
    </p>`;
  }

  if (past.length) {
    html += `
    <div class="events-separator">
      <span>Past Events</span>
    </div>
    <div class="card-grid events-past">${past.map(cardHtml).join("")}</div>`;
  }

  eventsContainer.innerHTML = html;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── MathCafe loader ──────────────────────────────────────────────────────────

const mcContent  = document.getElementById("mc-content");
const mcLoading  = document.getElementById("mc-loading");
const mcEmpty    = document.getElementById("mc-empty");

if (mcLoading) {
  loadMathCafe();
}

async function loadMathCafe() {
  try {
    const res = await fetch("data/mathcafe.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const facts = data.recent_facts || [];

    if (!facts.length) {
      mcLoading.style.display = "none";
      mcEmpty.style.display   = "block";
      return;
    }

    renderMathCafe(facts);
    mcLoading.style.display  = "none";
    mcContent.style.display  = "block";
  } catch (err) {
    mcLoading.style.display = "none";
    mcEmpty.style.display   = "block";
    console.warn("MathCafe load error:", err);
  }
}

function mcBadgeClass(category) {
  const map = { math: "mc-badge-math", cs: "mc-badge-cs", history: "mc-badge-history", science: "mc-badge-science" };
  return map[category] || "mc-badge-default";
}

function renderMathCafe(facts) {
  const latest = facts[0];
  const recent = facts.slice(1);

  // Latest card
  const latestCard = document.getElementById("mc-latest-card");
  if (latestCard && latest) {
    const imgHtml = (latest.needs_image && latest.image_filename)
      ? `<img src="data/${escHtml(latest.image_filename)}"
              alt="${escHtml(latest.title)}"
              style="max-width:100%; border-radius:8px; margin:0.8rem 0;"
              onerror="this.style.display='none'">`
      : "";
    latestCard.innerHTML = `
      <span class="mc-badge ${mcBadgeClass(latest.category)}">${escHtml(latest.category)}</span>
      <h2>${escHtml(latest.title)}</h2>
      ${imgHtml}
      <div class="mc-body">${escHtml(latest.body)}</div>
      <div class="mc-meta">Posted ${escHtml(latest.posted_date || "recently")}</div>
      ${latest.discussion
        ? `<a href="https://discord.gg/Ya4XvTE6A" class="mc-discord-btn" target="_blank" rel="noopener">
             💬 Join the discussion on Discord →
           </a>`
        : ""}`;
  }

  // Recent grid
  const grid = document.getElementById("mc-recent-grid");
  if (grid && recent.length) {
    grid.innerHTML = recent.map((f) => `
      <div class="mc-card">
        <span class="mc-badge ${mcBadgeClass(f.category)}">${escHtml(f.category)}</span>
        <h3>${escHtml(f.title)}</h3>
        <p class="mc-excerpt">${escHtml((f.body || "").slice(0, 100))}…</p>
        <div class="mc-meta">${escHtml(f.posted_date || "")}</div>
      </div>`).join("");
  } else if (grid) {
    grid.innerHTML = `<p style="color:var(--gray-mid);">No previous facts yet — check back tomorrow!</p>`;
  }
}

// ── MathCafe home-page preview ───────────────────────────────────────────────

const mcPreviewContainer = document.getElementById("mc-home-preview");

if (mcPreviewContainer) {
  loadMathCafePreview();
}

async function loadMathCafePreview() {
  try {
    const res = await fetch("data/mathcafe.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const latest = (data.recent_facts || [])[0];
    if (!latest) return;

    mcPreviewContainer.innerHTML = `
      <span class="mc-badge ${mcBadgeClass(latest.category)}">${escHtml(latest.category)}</span>
      <h3 style="margin:0.5rem 0 0.4rem;">${escHtml(latest.title)}</h3>
      <p style="color:#555; font-size:0.9rem; line-height:1.5;">
        ${escHtml((latest.body || "").slice(0, 120))}…
      </p>
      <a href="mathcafe.html" style="font-size:0.85rem; color:#8B4513; font-weight:600;">
        See all facts →
      </a>`;
  } catch (err) {
    console.warn("MathCafe preview error:", err);
  }
}
