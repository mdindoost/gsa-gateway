# FacultyFolio — Profile Pages: Show the Full Role Set (Leadership Fix)

**Date:** 2026-07-10
**Status:** Design — awaiting owner sign-off + senior-eng review (per CLAUDE.md hard gate)
**Scope:** FacultyFolio profile pages (`/p/<slug>.html`) — the person's title/appointment rendering
**Design input:** owner (format + placement decisions) + Fable (root-cause + refinements, 2026-07-10)

## 1. Motivation / the flaw

A person's FacultyFolio profile shows only their **home-faculty** title. But the KG stores
multiple `has_role` edges per person, and **`get_faculty` has no branch for the `admin`
(leadership) category — it silently drops those edges** (`facultyfolio/db.py`, the role loop
~lines 134-150 reads `faculty`/`joint`/`affiliated` only). So a person's leadership role —
Associate Dean, Chair, Director — is **invisible on their own profile**, even though the KG
knows it and the college hub already shows it. The hub is richer than the person's page, which
is backwards.

Concrete: David Bader's profile reads "Distinguished Professor, Data Science"; his
**Associate Dean** role (a real `admin@YWCC` edge, verbatim from NJIT's college admin page)
never appears. Joint & affiliated tiers are *already* shown today (header + Background
"Appointment" row), so **the only missing tier is leadership.**

**Guiding principle (owner):** everything in the KG is verbatim from an NJIT page — crawled or
manually added from an NJIT page. So surfacing all of it is not fabrication; the verbatim rule
is why we're *free* to show every role.

## 2. Goal

Profile pages reflect the person's **full role set** from the KG — home, leadership, joint,
affiliated — all four tiers, labeled, with **no repeated titles**. Two visible changes:

1. **A leadership line in the header card**, next to the name (where the joint line already
   renders): e.g. "Associate Dean · Ying Wu College of Computing".
2. **The Background "Appointment" row upgraded** from a run-on em-dash sentence into a
   structured, labeled, stacked list of all appointments.

Primary line stays the **home** title (a role someone *holds* ≠ who they *are*). Single-role
faculty (~85%) see essentially no change.

### Worked example — Bader
```
David Bader
Distinguished Professor, Data Science               ← primary line (home), unchanged
Associate Dean · Ying Wu College of Computing       ← NEW leadership line in the header

Background
  Appointment:
   • Distinguished Professor · Data Science     (home)
   • Associate Dean · Ying Wu College of Computing (leadership)
   • Joint appointment · Computer Science
```
One rank, one role, no repeated "Distinguished Professor"; every string verbatim from the KG.

## 3. Non-goals (YAGNI)

- No standalone "Roles & Appointments" section (owner chose to upgrade the existing Appointment
  row instead — leaner, single-role pages unchanged).
- No change to who gets a profile page (still `category='faculty'` home faculty only; pure-admin
  people like Dean Payton have no profile page).
- No new crawl, no schema change. Read-only KG additions.
- No rewording of any title — only SELECTION of which stored title to display.

## 4. Design

### 4.1 Data layer — `db.get_faculty` (`facultyfolio/db.py`)

**Add an `admin` branch to the existing role loop** and a generalized role-title selector.

Generalize the existing `_role_title` (today matches only "dean") to a shared selector over
role words:
```python
_ROLE_WORDS = ("dean", "chair", "director", "head", "provost", "president")

def _role_title(titles: list) -> str:
    """The role/leadership title from a (rank + role) titles list: the entry containing a role
    word; else the last entry. SELECTION only — never reword. Compound single strings like
    'Professor and Chair, Civil & Environmental Engineering' contain a role word and are shown
    whole (verbatim)."""
    for t in titles or []:
        low = t.lower()
        if any(w in low for w in _ROLE_WORDS):
            return t
    return (titles or [""])[-1]
```

In the role loop, collect leadership appointments (edge order):
```python
leadership = []   # [{"title": <role title>, "org": <expanded college/unit name>}]
...
elif e["category"] == "admin":
    short = _org_name(conn, e["dst_id"])
    org = config.COLLEGE_NAMES.get(short, short)   # expand acronym -> proper name (as _college_of does)
    role = _role_title(titles)
    if role:
        leadership.append({"title": role, "org": org})
```
Return a new key `"leadership": leadership` in the get_faculty dict (empty list when none).
`home_dept`, `joint_dept`, `affiliated_depts` are unchanged (already collected).

**Org expansion matters for suppression** (audit finding): the admin edge's org NODE name is the
acronym ("YWCC"), but leadership titles spell the college out ("Dean, Ying Wu College of
Computing"). Expanding via `config.COLLEGE_NAMES` FIRST makes both the display suffix and the
render-time substring suppression (§4.2) work: "Associate Dean" @ YWCC → "· Ying Wu College of
Computing" (appended); "Dean, Ying Wu College of Computing" → suppressed (title already contains
the expanded name). **Org-in-title suppression** itself is applied at RENDER time (§4.2).

### 4.2 Render layer — `facultyfolio/render.py`

Replace the run-on `_appointment(f)` (returns a string) with a structured builder
`appointment_lines(f) -> list[dict]` producing labeled, de-duplicated lines in tier order:

```python
def _org_suffix(title: str, org: str) -> str:
    """'· <org>' unless the verbatim title already names the org (casefold substring)."""
    if not org:
        return ""
    return "" if org.lower() in (title or "").lower() else f" · {org}"

def appointment_lines(f: dict) -> list:
    """Structured appointment list, tiers in order home → leadership → joint → affiliated.
    Each: {"text": str, "label": str}. No title repeats: home carries rank+dept; leadership
    carries the role title (+org unless embedded); joint/affiliated carry ORG ONLY (no title)."""
    out = []
    if f.get("home_dept"):
        rank = f"{f['title']} · {f['home_dept']}" if f.get("title") else f["home_dept"]
        out.append({"text": rank, "label": "home"})
    for L in f.get("leadership") or []:
        out.append({"text": f"{L['title']}{_org_suffix(L['title'], L['org'])}", "label": "leadership"})
    if f.get("joint_dept"):
        out.append({"text": f"Joint appointment · {f['joint_dept']}", "label": "joint"})
    for aff in f.get("affiliated_depts") or []:
        out.append({"text": f"Affiliated · {aff}", "label": "affiliated"})
    return out
```
- **Joint/affiliated carry NO title** (org + label only) — prevents "Distinguished Professor ·
  Computer Science" reading as a second primary chair (Fable risk #3).
- The **header leadership line** uses the same `leadership` list: render each as
  `"{title}{_org_suffix}"`; label styling matches the existing header joint line
  (`profile.html` line ~42, the `.uni`-style line).

The old `college` trailing clause of `_appointment` is dropped from the per-line list (college
is already implied by the org names + shown elsewhere); confirm no other caller depends on the
old string return. Keep a thin `_appointment` shim ONLY if another template/caller still needs
the sentence form — otherwise remove it.

### 4.3 Template — `facultyfolio/templates/profile.html`

- **Header card:** after the existing joint-appointment line (~line 42), add a leadership line
  per `f.leadership` entry (rendered via render helper), same visual treatment. Omitted when
  `leadership` is empty (honest-empty — the ~85% single-role case is visually unchanged).
- **Background "Appointment" row:** replace the single run-on string with the stacked list from
  `appointment_lines(f)` — one line per appointment, each with its label. A single-home person
  yields exactly one line (the home line), so their page looks as before.

### 4.4 Ordering & labels
- Tier order: **home → leadership → joint → affiliated** (leadership above cross-listings — a
  distinct role, not a shading of professorship; affiliated last, weakest tier). Deterministic
  edge-id order within a tier.
- Labels use human words — "home", "leadership", "joint", "affiliated" — never the raw
  category "admin" (reads as staff/IT to an academic). Matches the bot's shipped
  entity_card markers ("(joint appointment)"/"(affiliated)") for one mental model across surfaces.

## 5. Data-honesty / correctness

- **Verbatim** — every displayed title is a string already stored in the KG (from an NJIT page);
  we only SELECT which stored title to show and SUPPRESS a redundant org suffix. No rewording.
- **No repeated titles** — the rank appears once (home line); the role appears once (leadership
  line); joint/affiliated are org-only. Bader shows "Distinguished Professor" once, not thrice.
- **Honest-empty** — no leadership → no header leadership line, no extra appointment lines.
- **Primary line = home** — never promote a held role to the identity line.

## 6. Pre-build data audit — DONE (2026-07-10)

Ran the generalized `_role_title` over all 72 live `admin` edges, then intersected with faculty
(profile-page holders). Findings:

- **72 admin edges total**, but only **21 belong to faculty** (people who get a profile page).
  **All 21 select a genuine academic-leadership title** — Dean, Associate Dean, Chair, Department
  Chair, Interim Chair, Chairman, "Professor and Chair, <dept>" (compound, shown whole). No
  nonsensical selection, no rank-only fallback among the 21.
- The problematic non-leadership admin titles ("Assistant to Chair" — which false-matches the
  "chair" role word — "Administrative Coordinator", "Business Manager", plus Provost/President/VP)
  all belong to **non-faculty** with **no profile page**, so they never render. The
  role-word false-match on "Assistant to Chair" is therefore **benign** (no faculty holds it);
  no extra guard needed, but noted for the future.
- **Today only YWCC is published**, so the leadership line currently renders on exactly **3**
  pages: **Bader** & **Wu** ("Associate Dean · Ying Wu College of Computing") and **Payton**
  ("Dean, Ying Wu College of Computing", org suffix suppressed). The dept **chairs**
  Oria/Geller/Halper carry "Department Chair" on their **faculty** edge title (not an admin
  edge), so they already show it — unaffected by this change.
- The remaining 18 leaders are NCE/HCAD/MTSM/CSLA faculty — no FacultyFolio page yet; they'll
  render correctly (all clean) when those colleges publish. Minor deferred nuance: an
  "MTSM Administration" org node isn't in `COLLEGE_NAMES`, so a bare "Associate Dean" there would
  read "· MTSM Administration"; harmless and not published — revisit when MTSM goes live.

Conclusion: the `_role_title` generalization and org-expansion are safe to ship; no role-word
exclusions needed for the current + full-scale faculty population.

## 7. Testing

- `db.get_faculty`: returns `leadership` list for a person with an admin edge (Bader →
  `[{"title":"Associate Dean","org":"YWCC"}]`); empty for a plain faculty member; role-title
  selection picks the role entry from a `[rank, role]` list.
- `_role_title` (generalized): picks "Associate Dean" from `["Distinguished Professor","Associate Dean"]`;
  "Interim Chair" from `["Professor","Interim Chair"]`; a compound "Professor and Chair, X" whole;
  falls back to last entry when no role word.
- `render.appointment_lines`: tier order home→leadership→joint→affiliated; joint/affiliated
  lines carry NO title; org suffix suppressed when the title already contains the org
  ("Dean, Ying Wu College of Computing" → no "· Ying Wu College…" appended); a single-home
  person yields exactly one line.
- `render` header: a leader's profile shows the leadership line; a plain faculty profile does
  NOT (honest-empty); primary line stays the home title.
- Real-build spot check: `/p/bader.html` shows the header "Associate Dean" line and the stacked
  Appointment list; `/p/ikoutis.html` (single-role) visually unchanged.

## 8. Goals checklist (fill at PR time)

- [ ] `get_faculty` includes `admin`/leadership edges (`leadership` field)
- [ ] `_role_title` generalized to role words; verified against all 72 admin edges (§6)
- [ ] Header card shows leadership line(s); honest-empty for single-role
- [ ] Appointment row upgraded to structured stacked list, all four tiers, no repeated titles
- [ ] Org-in-title suppression; joint/affiliated org-only; labels human not "admin"
- [ ] Primary line stays home title
- [ ] Single-role profiles visually unchanged (regression check)

## 9. Deploy note

Batch with the pending **Brook Wu → "Associate Dean"** KG edit (already committed to the live
DB, backed up, not yet deployed): one rebuild + one Pages push covers both. Deploy is
owner-gated.

## 10. Known dependency (Fable risk #1)

The `affiliated` tier's producer is known-fragile: ~12 of 14 affiliated edges may revert to
`faculty` on the next `explore` re-crawl. Surfacing all roles makes such a revert *visible* on
the site (arguably a feature — it flags the data bug). Crawler is currently paused. Not this
design's problem to fix; flagged so it's a conscious dependency.
