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
  if (!events.length) {
    eventsContainer.innerHTML = `
      <p style="color:var(--gray-mid); text-align:center; padding:3rem 0;">
        No upcoming events right now — check back soon!
      </p>`;
    return;
  }

  const html = events
    .map(
      (ev) => `
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
    </div>`
    )
    .join("");

  eventsContainer.innerHTML = `<div class="card-grid">${html}</div>`;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
