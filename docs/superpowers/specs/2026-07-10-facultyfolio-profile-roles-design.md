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
- No change to who gets a profile page (still `category='faculty'` home faculty only; a truly
  pure-admin person — e.g. the Provost/President or the staff "Administrative Coordinator" titles,
  none of whom hold a faculty edge — has no page. NOTE: Dean Payton DOES have a page — she holds a
  faculty edge — so she is not an example of pure-admin).
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

def _visible_leadership(f: dict) -> list:
    """Leadership entries to SHOW: drop any whose title is already contained in the home title
    (casefold substring) — else Payton (home title 'Dean, Ying Wu College of Computing') and Wu
    (home 'Associate Dean for Academic Affairs') would repeat their role. SELECTION, not rewording.
    Shared by the header line AND appointment_lines so they never disagree."""
    home = (f.get("title") or "").lower()
    return [L for L in (f.get("leadership") or []) if L["title"].lower() not in home]

def appointment_lines(f: dict) -> list:
    """Structured appointment list, tiers in order home → leadership → joint → affiliated.
    Each: {"text": str, "label": str}. No title repeats: home carries rank+dept; leadership
    carries the role title (+org unless embedded); joint/affiliated carry ORG ONLY (no title).
    Tier LABELS are omitted when the list is a single line (a lone 'home' label is noise)."""
    out = []
    if f.get("home_dept"):
        rank = f"{f['title']} · {f['home_dept']}" if f.get("title") else f["home_dept"]
        out.append({"text": rank, "label": "home"})
    for L in _visible_leadership(f):
        out.append({"text": f"{L['title']}{_org_suffix(L['title'], L['org'])}", "label": "leadership"})
    if f.get("joint_dept"):
        out.append({"text": f"Joint appointment · {f['joint_dept']}", "label": "joint"})
    for aff in f.get("affiliated_depts") or []:
        out.append({"text": f"Affiliated · {aff}", "label": "affiliated"})
    if len(out) == 1:
        out[0] = {"text": out[0]["text"], "label": ""}     # single-line: no tier label
    return out
```
**Home-title containment suppression (Fable BLOCKER):** the `_visible_leadership` filter drops a
leadership entry whose title is already in the home title. Consequences, verified against live
data: **Payton** (home faculty title = "Dean, Ying Wu College of Computing") → leadership line
suppressed (already her home line); **Wu** (home = "Associate Dean for Academic Affairs") →
"Associate Dean" suppressed; **Bader** (home = "Distinguished Professor") → "Associate Dean"
line SHOWN. So today the leadership line renders on exactly **one** page (Bader) — the honest
number and precisely the bug we set out to fix. The SAME `_visible_leadership` feeds the header
line, so header and appointment list never disagree.
- **Joint/affiliated carry NO title** (org + label only) — prevents "Distinguished Professor ·
  Computer Science" reading as a second primary chair (Fable risk #3).
- The **header leadership line** uses `_visible_leadership(f)` (the SAME filter — never the raw
  `leadership` list): render each as `"{title}{_org_suffix(title, org)}"`; label styling matches
  the existing header joint line (`profile.html` line ~42, the `.uni`-style line).

`render._appointment` has **exactly one production caller** (`render_profile`, verified) — so it
is REMOVED (no shim needed); `render_profile` calls `appointment_lines(f)` instead and adds both
`leadership=_visible_leadership(f)` and the appointment list to the template context.

### 4.3 Template — `facultyfolio/templates/profile.html`

- **Header card:** after the existing joint-appointment line (~line 42), add a leadership line
  per `_visible_leadership(f)` entry, same `.uni`-style visual treatment. Omitted when the
  filtered list is empty (honest-empty — every single-role person AND leaders like Payton/Wu
  whose home title already states the role).
- **Background "Appointment" row:** replace the single run-on string with the stacked list from
  `appointment_lines(f)` — one line per appointment, label shown only when >1 line. A single-home
  person yields exactly one line (the home line, no label).

### 4.4 Ordering & labels
- Tier order: **home → leadership → joint → affiliated** (leadership above cross-listings — a
  distinct role, not a shading of professorship; affiliated last, weakest tier). Deterministic
  edge-id order within a tier.
- Labels use human words — "home", "leadership", "joint", "affiliated" — never the raw
  category "admin" (reads as staff/IT to an academic). Matches the bot's shipped
  entity_card markers ("(joint appointment)"/"(affiliated)") for one mental model across surfaces.
  A single-line list shows NO label.

**⚠️ Visible change to EVERY profile's Appointment row (owner: note before deploy).** The old
`_appointment` sentence — e.g. "Associate Professor, Computer Science, Ying Wu College of
Computing." — becomes a stacked list; a single-role person's row becomes "Associate Professor ·
Computer Science". So on all ~119 pages the separator changes (`, ` → ` · `) and the trailing
**college clause is dropped from this row** (it is still shown in the header's dept line,
`profile.html:43`, so no info is lost). This is intended (Fable/senior-eng reviewed) but it IS a
visible cosmetic change to every profile, not just leaders' — flagged for explicit owner sign-off.

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
- **Today only YWCC is published**, and after the home-title-containment suppression (§4.2) the
  new leadership line renders on exactly **1** page: **Bader** ("Associate Dean · Ying Wu College
  of Computing"). **Payton** (home faculty title already "Dean, Ying Wu College of Computing")
  and **Wu** (home "Associate Dean for Academic Affairs") have their role suppressed as a
  separate line — it already leads their page, no info lost. The dept **chairs** Oria/Geller/
  Halper carry "Department Chair" on their **faculty** edge title (not an admin edge), so they
  already show it — unaffected.
- The remaining 18 leaders are NCE/HCAD/MTSM/CSLA faculty — no FacultyFolio page yet; they'll
  render (all clean) when those colleges publish. Two deferred nuances, no action now: (a) an
  "MTSM Administration" org node isn't in `COLLEGE_NAMES`, so a bare "Associate Dean" there reads
  "· MTSM Administration"; (b) NCE dept chairs' admin edges hang off the **college** node, so
  Lieber's "Department Chair" would read "· Newark College of Engineering" (college, not his
  department) — same class as (a), revisit when NCE publishes.

Conclusion: the `_role_title` generalization and org-expansion are safe to ship; no role-word
exclusions needed for the current + full-scale faculty population.

**Caller-premise correction (senior-eng):** `get_faculty` is NOT only called for faculty —
`build._leadership_row` calls it on `college_leadership()`'s `category='admin'` people to build
college-hub cards. That path is INERT for this change: it renders via `render._lb_row`, which
ignores the new `leadership`/`appointment_lines` fields and never writes a `/p/` page. So the
"non-leadership admin titles never reach a rendered profile" conclusion holds — the precise
reason is "only `category='faculty'` slugs reach `build_one`/page-write," not "get_faculty is
only called for faculty."

**Edge notes (build against these):** (a) the `admin` branch must use the loop's already
`if t`-filtered `titles` list (db.py:139), not raw `eattrs["titles"]` — an empty titles list then
yields `_role_title([]) == ""` → `if role:` skips it (honest-empty). (b) No current FACULTY holds
2+ admin edges (only non-faculty Teik Lim has two), so the list-append handles multi-admin but is
untested against real multi-admin faculty — acceptable. (c) Loop is `ORDER BY id` → deterministic.

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
  person yields exactly one line with an EMPTY label.
- **`_visible_leadership` containment (the BLOCKER guard):** a leadership entry whose title is
  casefold-contained in the home title is dropped — Payton ("Dean, Ying Wu College of Computing"
  home) → no leadership line; Wu ("…Associate Dean for Academic Affairs" home) → "Associate Dean"
  dropped; Bader ("Distinguished Professor" home) → "Associate Dean" KEPT. Test all three.
- `render` header: Bader's profile shows the leadership line; Payton/Wu/plain-faculty do NOT
  (honest-empty via the containment filter); primary line stays the home title.
- **REWRITE two existing tests** that assert the old sentence form (they WILL fail otherwise):
  `tests/test_render.py::test_appointment_includes_affiliated` (expects "joint appointment in …"
  / "affiliated with …" → now "Joint appointment · …" / "Affiliated · …") and
  `::test_appointment_no_affiliated_unchanged` (old single-string form → one home line).
- Real-build spot check: `/p/bader.html` shows the header "Associate Dean" line + the stacked
  Appointment list; `/p/ikoutis.html` (single-role) shows ONE appointment line
  "Associate Professor · Computer Science" (see §4.4 — the college clause moves to the header).

## 8. Goals checklist (fill at PR time)

- [ ] `get_faculty` includes `admin`/leadership edges (`leadership` field)
- [ ] `_role_title` generalized to role words; verified against all 72 admin edges (§6)
- [ ] Header card shows leadership line(s); honest-empty for single-role
- [ ] Appointment row upgraded to structured stacked list, all four tiers, no repeated titles
- [ ] Org-in-title suppression; joint/affiliated org-only; labels human not "admin"
- [ ] Primary line stays home title
- [ ] `_visible_leadership` containment guard (Payton/Wu suppressed, Bader kept) — tested
- [ ] Single-role pages yield exactly ONE appointment line, no label (college moved to header) — NOT byte-unchanged; the row's format intentionally changes on every page (§4.4)
- [ ] Two old `_appointment` sentence-form tests rewritten (§7)

## 9. Deploy note

Batch with the pending **Brook Wu → "Associate Dean"** KG edit (already committed to the live
DB, backed up, not yet deployed): one rebuild + one Pages push covers both. Deploy is
owner-gated.

## 10. Known dependency (Fable risk #1)

The `affiliated` tier's producer is known-fragile: ~12 of 14 affiliated edges may revert to
`faculty` on the next `explore` re-crawl. Surfacing all roles makes such a revert *visible* on
the site (arguably a feature — it flags the data bug). Crawler is currently paused. Not this
design's problem to fix; flagged so it's a conscious dependency.

## 11. Review log (2026-07-10)

**Both reviewers independently caught the same BLOCKER** — a leadership line duplicating the
home title on Payton/Wu (live pages). Resolved before build: the `_visible_leadership`
containment filter (§4.2) drops a leadership entry whose title is casefold-contained in the home
title; shared by the header line and the appointment list. Net effect: leadership line renders
on 1 page today (Bader).

- **Senior-eng review** → "needs rework"; all points folded in: the BLOCKER (above); §3 Payton
  factual fix; §6 caller-premise correction (hub `_leadership_row` calls `get_faculty` but is
  inert); "visually unchanged" corrected to "one line, college moved to header" + explicit owner
  flag (§4.4); two breaking `_appointment` tests named for rewrite (§7); empty-titles / multi-admin
  edge notes (§6). Audit numbers independently re-verified (21 faculty, all clean).
- **Fable** (owner-delegated sign-off) → GO-WITH-CHANGES; same BLOCKER + the count correction,
  §3 fix, §7 org-value fix, single-line label omission — all applied. Fable: "once edits land,
  approved to build; a diff of the spec changes is sufficient, no full re-pass."
