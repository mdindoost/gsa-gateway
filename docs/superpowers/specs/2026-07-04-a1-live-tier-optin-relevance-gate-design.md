# A1 — gate the live njit.edu tier (opt-in confirm + relevance-gate) design

**Date:** 2026-07-04
**Status:** DRAFT → Fable design-review → build TDD → Fable diff → ship. Owner set the direction = "BOTH".
**Split of:** accuracy roadmap (`project_pipeline_accuracy_review`), A1 — the live tier is the only un-gated
answer tier AND the WS4 gate-reject retries into it (least-checked tier gets the hardest questions).

## Problem (current live behavior)
On a KB/KG miss the bot **auto-fires** a Brave njit.edu search, fetches the top page, and serves an
extractive answer — **un-gated** (the WS4 faithfulness gate is explicitly skipped for live:
`message_handler.py:1008 if used_live: pass`). Two auto-fire sites:
- **primary-miss** (`:990`): `primary_miss` (no chunk OR top rerank rel < `LIVE_THRESHOLD`) → office tier →
  deep-fallback → auto `live_search(base_q)`.
- **gate-reject rescue** (`:1047`): when WS4 rejects a composed KB answer as unfaithful, it auto-fires live
  BEFORE abstaining ("never-withhold").
So the hardest queries — the ones just judged unanswerable from the KB — get funneled into the least-checked
tier, and whatever the top page yields is served.

**Concrete failure:** "PhD in Data Science — when do I defend my qual exam?" → KB miss → auto-fired live →
Brave returned the **DS-PhD program-overview** page → served program prose, NOT qual-exam timing. Real NJIT
content (so it reads authoritative), but the WRONG page → confident non-answer.

## Direction (owner) = BOTH
1. **Opt-in confirm** — a genuine miss no longer auto-searches; it OFFERS "search njit.edu?" and waits for
   consent. No live call without opt-in.
2. **Relevance-gate the result** — before serving a live answer, verify the fetched page actually answers
   the question; off-target → honest "couldn't find a specific answer" + the page link.

## Existing machinery to reuse (recon)
- **Offer→tap→search** already exists: `MessageResponse.offer_live_search` → connector attaches a button →
  tap → `handler.live_search(question)` (telegram_connector `:106/:454/:503`). Today it fires only on a
  DEFLECTION (`deflection.looks_like_deflection` + `:1096`). **BUT the button is Telegram-ONLY** (Discord/
  GroupMe don't render it).
- **Explicit search** everywhere: `parse_explicit_live_search` catches "search njit for X" →
  `_answer_explicit_live` → `live_search` (`:344/:753`). This is the universal, all-platform opt-in path.
- **`live_search`** (`:739`) is the SINGLE seam (provider wiring + feature gate) — both the auto path and the
  tap path go through it, so adding the relevance-gate HERE covers every live call at once.
- **WS4 gate** (`answer_gate.gate2_prompt`/`parse_gate2`, `faithfulness`) is the answerability judge KB
  answers already pass — the natural relevance-gate for live (closes the "only un-gated tier" gap in the
  same move).

## Design

### (1) Opt-in — stop auto-firing; offer instead
Remove the two auto-fire `live_search` calls (`:990` primary-miss, `:1047` gate-reject). In their place:
- Produce a **deflection** (`_useful_abstain` / `_KB_MISS_RESPONSE`, `is_abstain=True`) with
  `offer_live_search=True` so the existing offer path fires.
- **Cross-platform opt-in:** Telegram renders the button (existing). Discord/GroupMe (no button) get a
  one-line hint appended to the deflection: *"Want me to check NJIT's website? Ask: 'search njit for
  <your question>'."* — which routes through the EXISTING `parse_explicit_live_search` →
  `_answer_explicit_live` path (no new pending infra). So opt-in is universal: button on TG, explicit-ask
  everywhere.
- **Never-withhold note:** the owner is knowingly trading auto-never-withhold for opt-in-with-consent — the
  content is still one tap/one line away, so it isn't withheld, just consented. This is the owner's explicit
  A1 direction and supersedes the auto-fire added 2026-06-20. (The EXPLICIT "search njit for X" and the
  Telegram tap remain direct — the user already consented.)

### (2) Relevance-gate — make live a gated tier
Add an answerability check to the live extract, in the provider-isolated `maybe_answer_live` loop
(`live_fallback.py`) via an injected async `relevance_ok(question, spans) -> bool` (so the module stays
provider-clean). Bump **`max_pages` 2→3** (Brave `search()` already returns 3 URLs → zero extra quota; one
more fetch+extract only on a double-miss, where the user already consented to wait — captures the realistic
recall of the owner's "top 3" idea WITHOUT multi-page synthesis). After `ground_spans` yields verbatim spans,
run `relevance_ok`; **fail → `continue` to the next candidate page**; **all candidates fail → the top-3-links
degrade (below).**
- `relevance_ok` wraps the **WS4 Gate-2 answerability** verdict (`gate2_prompt`/`parse_gate2`) on
  (question, RAW extracted spans). "Does this extract actually answer the question?" — exactly the check that
  distinguishes "qual-exam timing" from "program overview". Reuses the same judge KB answers pass, so live
  is now gated like every other tier.
- **Off-target degrade = TOP-3 LINKS (owner idea B, Fable-endorsed — replaces honest+1-link):** when the
  loop exhausts with NO grounded+relevant page, return the top-3 Brave njit.edu URLs as an honest link list:
  *"I couldn't find a direct answer on njit.edu. The closest pages: 1) … 2) … 3) …"*. ZERO hallucination
  (URLs are verbatim from Brave, no LLM in this path), honest, and ACTIONABLE (the user consented to a
  search, so a link-list lets them finish the job — strictly better than a bare "couldn't find + 1 link").
  `maybe_answer_live` already holds `urls` when the loop falls through → return a distinct
  `LiveLinks(urls[:3])` result (NOT a `LiveAnswer` — keep the type distinct so eval/analytics separate real
  answers from degrades, and the gate-retry site still counts "live tried"). "Closest pages" framing claims
  proximity, not answers, so a tangential top-3 stays honest.
  - **Rejected (Fable):** owner's Variant A (feed top-3 to the LLM, synthesize one answer) — breaks the
    extractive/verbatim hard line (cross-page blend/paraphrase, unattributable claims, no single honest
    source link). The `max_pages=3` bump captures its realistic recall safely; multi-page-EXTRACTIVE-merge
    stays a deferred option if eval later shows answers that genuinely span pages.
  - **Nothing-found (no candidate URLs at all):** stays `None` → today's `LIVE_NOT_FOUND_MSG` (the true
    empty-search case; distinct from off-target-with-links).

### Flags (gated rollout, independent backout)
- `LIVE_AUTOFIRE` (default **True** = current behavior; flip **False** to enable opt-in). Flag-off (False)
  = the new opt-in behavior; default True ships with zero behavior change, owner flips after verifying.
- `LIVE_RELEVANCE_GATE` (default **off**; flip **on**). Pure-safety addition (drops off-target live answers);
  independent flag so it can go on before/after the opt-in flip and back out alone.
- Both default to CURRENT behavior on deploy (per the gated-workflow pattern); owner flips each after
  spot-checking. `LIVE_ENABLED=0` remains the master kill-switch.

## Scope boundaries
- **In:** the two auto-fire removals → offer; the cross-platform opt-in hint; the relevance-gate in
  `live_search`/`maybe_answer_live`; the off-target-with-link degrade; two flags.
- **Out (noted):** adding a real Discord/GroupMe button (the explicit-ask hint covers them; a native button
  is a connector enhancement, separate). Tuning Gate-2's answerability prompt for live specifically (reuse
  as-is first; tune only if measured misses). Multi-provider live (still Brave-isolated).

## Invariants / safety
- **Never break the answer path:** a relevance-gate fault (LLM/regex error) must default to KEEP-or-degrade,
  never crash — mirror the WS4 `try/except → keep` guard.
- **No fabrication:** live stays extractive (verbatim spans only); the gate only ADDS a drop, never invents.
- **Flag-off = today:** with `LIVE_AUTOFIRE=True` + `LIVE_RELEVANCE_GATE=off`, behavior is byte-identical to
  current (both new paths inert).
- **Explicit + tap stay direct** (user consented) but now ALSO relevance-gated (consistent gating).
- **GSA-equal / verbatim:** unchanged; the source link still covers staleness.

## Tests (TDD)
- **Opt-in:** primary-miss with `LIVE_AUTOFIRE=False` → NO `live_search` call, response is a deflection with
  `offer_live_search=True` (+ hint line on non-Telegram); with `LIVE_AUTOFIRE=True` → auto-fires (unchanged).
- **Gate-reject:** WS4 rejects + `LIVE_AUTOFIRE=False` → abstain+offer, no auto-live; `=True` → auto-live
  rescue (unchanged).
- **Relevance-gate:** a live extract that doesn't answer the question (mock Gate-2 NOT_ANSWERED) → dropped
  (tries next page, then the top-3-links degrade); an answering extract (Gate-2 answered) → served.
  With `LIVE_RELEVANCE_GATE=off` → served un-gated (unchanged).
- **max_pages=3:** page 1 & 2 off-target but page 3 answers → served (recall gain); all 3 off-target → links.
- **Top-3-links degrade:** all candidates off-target (or none grounded) BUT ≥1 URL existed → `LiveLinks`
  with the top-3 URLs, `is_abstain=True`, `abstain_reason="live-offtarget"`, `is_live=False`, no 🌐 prefix;
  each consumer (incl. TG tap) renders the link list. No candidate URLs at all → `None` → `LIVE_NOT_FOUND_MSG`.
- **Universal opt-in:** the explicit "search njit for X" path still runs direct AND is now relevance-gated.
- **Fault safety:** relevance_ok raises → keep/degrade, never crash.
- **Flag-off identity:** both flags at current defaults → existing tests unchanged (no regression).
- Add the qual-exam and a known-good live case to `eval/questions.txt`.

## Open decisions for the owner (before build)
1. **Flag defaults on deploy** — ship both defaulting to CURRENT (autofire on, gate off) and you flip after
   verifying (recommended, matches the gated pattern; flip GATE first, then OPTIN)? Or ship with the
   relevance-gate ON by default (it's pure safety) and only the opt-in behind a flip?
2. **Off-target degrade — RESOLVED (owner 2026-07-04):** TOP-3 LINKS (`LiveLinks`), not honest+1-link.
3. **Discord/GroupMe opt-in** — the explicit-ask hint line (in scope, no new infra), or the PendingAction
   "yes" route (reuse followup-resume, the real cross-platform upgrade), or a native Discord button (later)?

## Fable design-review hardening (APPROVE-WITH-CHANGES — folded in)
- **B1 — primary-miss must not early-deflect the WEAK-CHUNKS subcase.** `primary_miss` covers TWO states:
  no-chunks AND chunks-with-rel<`LIVE_THRESHOLD`. Under opt-in, only REMOVE the auto `live_search` call —
  the weak-chunks state must still fall through to compose+WS4-gate (never-withhold; the gate judges weak
  answers, not the threshold). deflection+offer arises ONLY where the flow already ends in one: no-chunks →
  `_KB_MISS_RESPONSE`+offer, gate-reject → `_useful_abstain`+offer. Do NOT deflect at `:990`.
- **B2 — `:1059 attempted_live=True` (gate-abstain) defeats the offer.** The offer at `:1096` needs
  `not attempted_live`; that line must become conditional on the autofire path having actually RUN, else the
  promised "abstain+offer" never renders under `LIVE_AUTOFIRE=False`.
- **B3 — off-target `LiveLinks` mapping, pinned across all 4 consumers** (auto `:993`, rescue `:1047`,
  `_answer_explicit_live:757`, TG tap `telegram_connector:503` which renders the live result directly): a
  `LiveLinks` (top-3 URLs) ⇒ `is_abstain=True, abstain_reason="live-offtarget", is_live=False`; the text is
  the "closest pages: 1)…2)…3)…" list, NO "🌐 Live from NJIT's website" prefix. Each consumer must handle the
  new `LiveLinks` type (vs `LiveAnswer` vs `None`) — the TG tap renders LiveAnswer directly today, so it needs
  an explicit `LiveLinks` branch. Critical: `eval_run.classify` checks `is_live` FIRST — off-target with
  `is_live=True` would corrupt the live-coverage metric (it's a deflect, not live coverage).
- **B4 — `relevance_ok` inputs + failure semantics.** Judge the RAW spans list (pre-`_format`, no 🌐
  boilerplate). Mirror `_faithfulness_gate` exactly: temp 0.0, pass `num_predict`/`num_ctx`, `fmt="json"`;
  transport None → KEEP (QW-A2); parse-fail → KEEP (answer-biased default); **PARTIALLY_SUPPORTED → SERVE**;
  exception → KEEP. Add the quote-grounding post-check (`verify_support`/robust grounding vs the spans) —
  label-only trusts a hallucinated FULLY_SUPPORTED; grounding is ~free (spans are verbatim page text).
- **N1** best-page URL = the first candidate that yielded grounded spans; no candidate yielded spans → None
  (today's `LIVE_NOT_FOUND_MSG`, the true nothing-found case). **N2** platform-gate the hint
  (`req.platform != "telegram"`) so TG doesn't get button+hint double-offer. **N4** eval note: autofire-off
  reclassifies former "live" as "deflect" — coverage drops BY DESIGN; either accept the new baseline or add
  a "deflect+offer" eval class. **N5** flag naming inverts the repo `*_ENABLED` convention — use `LIVE_OPTIN`
  (default 0 = current autofire) instead of `LIVE_AUTOFIRE`, or a loud config comment.
- **FLIP ORDER (Fable, important):** flip `LIVE_RELEVANCE_GATE=on` FIRST, watch real traffic, THEN
  `LIVE_OPTIN=1`. The un-gatedness (not the autonomy) caused the qual-exam failure — gated-auto
  (`autofire on + gate on`) may already kill it, and the owner can legitimately stop there keeping
  never-withhold automatic. `autofire-on + gate-on` is a GOOD state; only `optin + gate-off` is awkward.

## Owner-decision recommendations (Fable)
- **(a) Flag defaults:** ship both at current behavior; flip `LIVE_RELEVANCE_GATE` on first (checkpoint on
  real traffic), then `LIVE_OPTIN` — the owner may stop at gated-auto if it already fixes the failure.
- **(b) Off-target degrade:** honest message + link (the live twin of `_useful_abstain`; the closest page is
  often the right neighborhood). Conditional on B3's flag mapping.
- **(c) Cross-platform opt-in:** explicit-ask hint NOW (zero infra); the real upgrade is the **PendingAction
  "yes"** route (reuse the built-but-flag-off followup-resume so a natural "yes" fires `live_search` — beats
  retyping the whole question on mobile), NOT a native Discord button. Gated on `FOLLOWUP_RESUME_ENABLED`.

## Goals checklist (shipped/deferred) — BUILD COMPLETE 2026-07-04
- Opt-in: guard both auto-fire sites behind `not LIVE_OPTIN` → deflect+offer; cross-platform hint — ✅ SHIPPED
  (primary-miss `:1032`, gate-reject rescue `:1094`; B1 weak-chunks fall-through PRESERVED — only the
  auto `live_search` call is gated, no early deflect at `:990`; N2 hint `:1157`, `req.platform != "telegram"`)
- Relevance-gate live via WS4 Gate-2 answerability in the single seam — ✅ SHIPPED (`_live_relevance_ok`,
  reuses `gate2_prompt`/`parse_gate2`/`faith.decide_after_gate2` → grounding post-check per B4; answer-biased
  KEEP on transport-None/parse-fail/exception; wired in `live_search` under `LIVE_RELEVANCE_GATE`)
- `max_pages` 2→3 (owner idea A's safe recall, no synthesis) — ✅ SHIPPED (`3 if gate_on else 2`)
- Off-target degrade = TOP-3 LINKS `LiveLinks` (owner idea B) — ✅ SHIPPED (`LiveLinks` dataclass,
  `degrade_links` returns `urls[:3]`; B3 mapping pinned across all 4 consumers — auto-fire, gate-rescue,
  `_answer_explicit_live`, TG tap — each `is_abstain=True, abstain_reason="live-offtarget", is_live=False`,
  `_live_links_text` render, no 🌐 prefix)
- Two flags, flag-off = current — ✅ SHIPPED (`LIVE_OPTIN`, `LIVE_RELEVANCE_GATE`, both default off = today;
  N5 naming — `LIVE_OPTIN` not `LIVE_AUTOFIRE`)
- Native Discord/GroupMe button — DEFERRED (explicit-ask covers it; PendingAction "yes" is the real upgrade)
- Multi-page EXTRACTIVE merge (owner idea A, hard-line-safe form) — DEFERRED (revisit if eval shows spans-pages)
- Variant A SYNTHESIS — REJECTED (breaks the extractive/verbatim hard line)
- Gate-2 prompt tuning for live — DEFERRED (reuse first)

## Tests (built)
- `bot/tests/test_a1_live_relevance.py` (7) — Wave 1: `maybe_answer_live` relevance-gate + degrade + max_pages.
- `bot/tests/test_a1_optin_handler.py` (6) — Wave 2: opt-in suppresses auto-fire; N2 hint platform-gate;
  off-target auto-fire → `LiveLinks` deflection.
- `test_answer_gate_wiring.py` updated: `live_search` mock now a real `LiveAnswer` (contract is now
  `LiveAnswer | LiveLinks | None`, handler branches on isinstance).
- Regression: 117 pass across live/gate/handler/connector/deflection suites (flag-off = identical).
