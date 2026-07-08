# PROJECT MEMORY — Oracle Processing-Debt

> Living state for THIS project only. Read first on resume; update on every change. Keep out of global
> memory (owner points Claude to this folder). Convert relative dates to absolute.

═══════════════════════════════════════════════════════════════════════════════════════════════
▶▶▶ EXACT RESUME STATE @ 2026-07-07 (SESSION 3 — later; owner cue "processing debt" = START HERE) ◀◀◀
═══════════════════════════════════════════════════════════════════════════════════════════════
**PIVOT 2026-07-08 (owner): DON'T touch the card. The `entity_mentions` tags now power NEW person-FACET
questions ("Oria news", "Deek awards", "tell me more about X", "what is X involved in") — each its own
clean verbatim answer. Card stays byte-identical. LOCATE-vs-ANSWER: a new question LOCATES owned prose.**

**⛳ WHERE TO RESUME (session 3, 2026-07-08 late): ✅ FACET BUILD DEPLOYED LIVE + VERIFIED. Step-4 proof is a DECISION.**
- **DEPLOYED LIVE 2026-07-08 (owner "Go"):** (1) `create_all('gsa_gateway.db')` created the `entity_mentions` table.
  (2) `tag_entity_mentions.py --commit --audit scratchpad/em_live_audit.csv` → **114 mentions / 58 people** live
  (hardened_backup `.backups/gsa_gateway.20260708-0804*.entity_mentions.db` auto-taken). (3) `restart.sh` → all 4
  services UP on new code, no startup errors. (4) LIVE proof via real router→serving: "Vincent Oria news"→news
  (honest-empty, Oria has no tagged news — correct), "Fadi Deek awards"→full verbatim award list, "tell me more
  about Vincent Oria"→the full 1389-ch bio as its OWN answer (the flagship win — was overflowing the card),
  "what is Vincent Oria involved in"→MMI items, "who is/tell me about Vincent Oria"→entity_card UNCHANGED.
- **STEP-4 RESOLVED (owner chose (a) 2026-07-08):** accept the live qualitative proof as sufficient. NO Set-A re-run
  (it was the wrong instrument — Set-A holds ORIGINAL identity/research Qs whose answers the facet fix leaves UNCHANGED
  by design; it ADDS new answerable Qs, so Set-A would show ~0 movement. A real number would need facet-shaped oracle
  questions = Brave $, declined). The facet fix is DONE.
- **⏭ ONLY REMAINING GATE = owner's merge-to-main decision.** Facets are DEPLOYED LIVE from branch
  feat/processing-debt-pilot (that's the running code); branch is UNMERGED to main, same as the area-expansion pilot.
  Merge is the owner's call whenever he wants to consolidate.
- **Minor polish noted (not blockers):** involvement lists FAQ *titles* verbatim (phrased as questions) — truthful +
  source-linked but slightly awkward; could map to nicer labels later.

▼ superseded: pre-deploy "awaiting go" state ▼
**✅ FACET BUILD COMPLETE + TDD-GREEN. Awaiting owner deploy go.**
- **FACET BUILD (the pivot) — committed on feat/processing-debt-pilot:** delta-spec
  `docs/superpowers/specs/2026-07-08-person-facet-questions-delta-design.md` (Fable-reviewed §9) → skills
  e138d5f (`awards/news/bio/involvement_of_person` in entity.py) → serving e23e65a (4 in `_DETERMINISTIC_SKILLS`)
  → **router afdfd57** (`_AWARDS/_NEWS/_INVOLVEMENT/_BIO_CUE` + `_FACET_CUE` trigger + `_facets_on()` gate +
  `_person_skill` dispatch awards→news→involvement→bio, shadowed by research/paper branches). `_BIO_CUE`
  tightened to REQUIRE "more" so "tell me about X" stays the CARD (deviation from spec §3, recorded, STRENGTHENS
  card-unchanged). Flags: `PERSON_FACETS_ENABLED` default ON; `PERSON_ADDENDUM_ENABLED` flipped default OFF
  (card-addendum path retired inert). eval/questions.txt: 4 facet Qs added.
- **TESTS:** 32 router (F1 `_extract_area` mines no cue word · F2 recent/latest shadowed by research/papers ·
  4 facet routes · card unchanged · gate-off restores card · no-person→RAG) + 6 skill + 13 serving = 124 green;
  broader unified-router/handler regression 32 green. 2 wider failures PROVEN pre-existing (identical on afdfd57~1:
  qwen-1024 embed-dim + a surname-resolution fixture — NOT mine).
- **⏭ NEXT MOVE = owner deploy go, then:** (1) LIVE gated tag `python scripts/tag_entity_mentions.py --commit
  --audit out/em_audit.csv` (hardened_backup auto; table ensured via restart's create_all). (2) `bash scripts/restart.sh`
  (code deploy; PERSON_FACETS_ENABLED=1). (3) Dev-copy proof: "Oria news"/"Deek awards"/"tell me more about Oria" →
  real deterministic answers. (4) $0 Set-A re-run proves debt moved. NOTE: bio-facet fires ONLY once bios are tagged
  (title-match faq) — the tagger run in step 1 supplies them.
- **⚠ Known: verbose SURNAME-only facet phrasing ("what awards has Oria won", 5 tokens) is blocked by the
  `_resolve_surname` ≤4-content-token guard → use FULL name (eval does). Short surname forms ("Koutis awards") work.**

▼ superseded: card-addendum build (pre-pivot) ▼
**BUILT + TDD-GREEN + DEV-COPY-VERIFIED. SUPERSEDED by the facet pivot above (addendum now inert, flag OFF).**
- **BUILD COMPLETE — 7 tasks, all committed on feat/processing-debt-pilot** (spec 5bd3256/f751e50 → plan 0527543 →
  T1 5af0477 schema · T2 d9e9090 gate · T3 bbd2c5e build · T4 66ab49e runner · T5 2998866 addendum+flags · T6
  0d4cc38 wiring · T7 0e31d1a gold+eval). NEW: `v2/core/ingestion/entity_mentions.py`, `scripts/tag_entity_mentions.py`,
  `scripts/eval_entity_mentions.py`, `entity_mentions` table (knowledge schema), `structured_answer.build_person_addendum`
  + `render_addendum`, `_person_addendum_payload`/`_cap` + threaded addendum in message_handler, flags in bot/config.py.
- **TESTS:** ~26 new tests green + targeted regression of ALL message_handler paths = **161 passed, 1 xfailed**. The
  wider suite's ~92 failures are PROVEN pre-existing (identical on clean e390c0b — qwen-1024 embed-dim migration +
  live-DB fixtures, NOT mine). Also fixed 3 pre-existing arity-drift test mocks (person_names 4-tuple never updated).
- **DEV-COPY AUDIT (gsa_gateway.db→scratchpad/dev.db, gated):** tagger = **114 mentions / 58 people** (conservative;
  anti-roster held). Audit CSV = `scratchpad/em_audit.csv`. FLAGSHIP WORKS: id=64 "Who is Prof. Vincent Oria?" →
  Oria (title, 1.0); MMI FAQs → Oria+Houle+Sun+Dindoost correctly. Deek AWARDS (Tier-1) render perfectly.
- **⏭ NEXT MOVE = owner deploy go, then:** (1) LIVE gated tag `python scripts/tag_entity_mentions.py --commit --audit
  out/em_audit.csv` (hardened_backup auto; ensures the table via restart's create_all first, OR run the ENTITY_MENTIONS
  DDL). (2) `bash scripts/restart.sh` (code deploy; PERSON_ADDENDUM_ENABLED=1 awards-live, PERSON_MENTIONS_ENABLED=0).
  (3) Owner eyeballs em_audit.csv → flip PERSON_MENTIONS_ENABLED=1 + restart. (4) $0 Set-A re-run proves debt moved.
- **⚠ TWO AUDIT-REVIEW FINDINGS (tuning, not blockers — why mentions ships OFF):** (a) LONG bios (Oria 1389ch, no
  source_url) are OMITTED on Discord (2000-cap, verbatim-never-partial → no pointer w/o url); show FULLY on Telegram
  (4096). Multi-message split deferred (R10). (b) College `news` NEWSROLL pages (title just "News", id 39035/39061…)
  are noisy Tier-2 candidates for Deek — consider excluding generic-title aggregation pages before flipping mentions ON.

▼ superseded session-3-earlier resume (spec-written, reviews-dispatched) ▼
**spec written, two reviews DISPATCHED (background), awaiting findings.**
- **SPEC:** `docs/superpowers/specs/2026-07-07-person-entity-mentions-tagging-design.md` (committed **5bd3256** on
  feat/processing-debt-pilot). Approach EVOLVED from "query-time gate" → owner's better idea: **tag once, offline.**
- **THE DESIGN (owner-driven, Fable-endorsed):** an offline **`entity_mentions` tagger** (phase-1 = PEOPLE, built
  reusable for offices/colleges/topics later) that resolves which Person node(s) each KB item is ABOUT — gate =
  title fast-path → both-names-whole-word + anti-roster (count OTHER known person-names in item) + namesake→abstain;
  deterministic, LLM-free by default. Writes a NEW many-to-many `entity_mentions` table + an audit CSV; gated
  (hardened_backup + --commit). Then ADDITIVE VERBATIM surfacing on the person card in the SHARED
  `structured_answer` layer (new `person_addendum()` beside `deterministic_suffix`, so BOTH answer paths inherit it —
  Fable caught that message_handler.py:438 is the RARE legacy path): Tier-1 id-linked AWARDS + Tier-2 tagged prose
  (curated bio/news), length-budgeted for Discord 2000, flag-gated w/ independent Tier-2 kill switch. **RAG STAYS**
  (Fable's LOCATE-vs-ANSWER ruling: tags LOCATE, never ANSWER; RAG = the open-vocabulary coverage floor; deleting it
  turns every taxonomy gap into a confident blank).
- **⏭ NEXT MOVE:** (1) collect the senior-eng review (bg agent **a47fe47d63e2e6e06**) + Fable's review+3-open-Q
  ruling (Fable agent **ab4947e27342bd8ee**, owner delegated the design Qs + sign-off to Fable — Fable's OK = owner
  approval this step). (2) Fold findings into the spec (rev). (3) If Fable = SHIP → autopilot: writing-plans → TDD
  build (new `v2/core/ingestion/entity_mentions.py` + `scripts/tag_entity_mentions.py` + `entity_mentions` table in
  schema.py + `structured_answer.person_addendum` + card render + flags in bot/config.py + gold gate
  `scripts/eval_entity_mentions.py`) → show diff → deploy → **$0 cached Set-A re-run proves debt moved**.
- **3 OPEN Qs delegated to Fable (spec §13):** (a) key entity_mentions by item_id [rebuild post-crawl] vs
  natural_key [stable]; (b) merge flag defaults (lean: awards ON, mentions OFF-until-audit); (c) include crawler
  `about` bios in Tier-2 or crawler-verbatim+curated only.

**KEY EVIDENCE gathered session 3 (grounds the fix):**
- Live bot person-answer is ALREADY rich (roles/contact/research-areas/education/courses/Scholar links+papers) —
  I had OVER-claimed "drops everything." What it ACTUALLY drops for Oria = the curated bio **KB id=64** facts: MMI
  Workshop Chair 2025/2026, visiting professorships, major service, + **two awards** (2014 YWCC Research, 2015 ACM
  SIGMOD Test-of-Time). Oria has **ZERO award-TYPE rows** — his awards live ONLY in bio prose → for Oria, Tier-2
  (tagged prose) is the WHOLE win; Tier-1 awards does nothing for him. Awards are a FACULTY-only feature (87 people,
  all crawler; GSA officers/students have none).
- Naive tagging is unusable: `content LIKE '%Oria%'` = 712 rows ~96% false-pos ("memORIAl", policy PDFs);
  both-names-whole-word → 25, still w/ roster/co-author noise → hence the anti-roster + namesake gate. Probes:
  `scratchpad/kb_prose_probe.py` + `kb_prose_probe2.py`.
- **4-way LLM comparison** (prompt: "Who is Vincent Oria, the professor at NJIT…? Describe his role, research, and
  notable achievements"): Brave = stale metrics (h15/2k) but had awards; ChatGPT = best web answer, well-hedged, BUT
  MISSED his awards + MMI + exact degrees/metrics, 1 unverified "studied physics" claim; **Claude+web FABRICATED his
  education** (namesake conflation → fake Rutgers/Bucharest PhD); us = only column current on metrics (h23/3.6k) AND
  correct on education (verbatim) AND holds MMI. Punchline for the "complete AND correct" debate: we're the only one
  correct on THIS person; the fix makes us complete too w/o the fabrication risk. (Source gaps it exposed, deferred:
  researchwith.njit.edu grants/projects + the "first Black CS chair" news page — a CRAWL gap, not a tagging gap.)
- **FacultyFolio = OUT OF SCOPE** (owner: "don't bring FacultyFolio, it complicates things"). Rollout = faculty →
  students → staff.

▼ prior session-2 resume block (KG+KB "NEXT MOVE" — now SUPERSEDED by the spec above; kept for provenance) ▼
**Owner ended the session here on purpose; on the cue "processing debt", resume at THE NEXT MOVE. Don't re-derive
the finished work below — it's DONE.**

**⛳ WHERE TO RESUME: skip straight to "⏭⏭ THE NEXT MOVE" below = the KG+KB WINNER-TAKE-ALL fix (Fable's #1).**
The last working session (2026-07-07 PM) was a **DETOUR, now fully DONE** — a VRAM/model diet the owner asked for
(shared 16GB GPU with his ML research). It shipped + deployed + merged to main; nothing pending. It did NOT advance
the processing-debt thread itself. Recorded in the "🟢 INFRA CHANGE THIS SESSION" block below for provenance — read
it ONLY if you need the current model/VRAM facts (2 models now: granite gen + qwen embed; area-verify = granite4:tiny-h,
NOT gemma). Then start the KG+KB fix from brainstorming.

**✅✅ FIX #1 SHIPPED, LIVE, MERGED, PUSHED (2026-07-07): LLM-VERIFIED AREA EXPANSION.**
  Umbrella research queries ("who works on cyber security") went **1 → 13 faculty** (was surfacing 1 of ~15
  owned). embed query → KNN + token-overlap candidate shortlist over 1,379 owned area tags → **gemma3:12b**
  verify (chunked 10, strict few-shot prompt v3) picks the same-field subset → existing deterministic SQL.
  Enumerate skills expand; per-person yes/no gained a **"related"** honest-partial verdict (never a false "no").
  Fail-safe to exact; flag **AREA_EXPAND_ENABLED** (default ON, unset in .env); OPS-DB cache keyed by
  area+vocab-sig+model+prompt-ver+K+chunk. Module = `v2/core/retrieval/area_expand.py` (+ area_cache.py; hooks in
  skills.py/structured_answer.py/embedder.py/schema.py). Gold gate `scripts/eval_area_verify.py` (55 pairs incl.
  measured traps): prod-shape **precision 0.923 / recall 0.889** (passes ≥0.9). Full gate: design → senior-eng +
  Fable(RAG) review → 10 TDD tasks (each reviewed; caught+fixed cache-poisoning, index off-by-one, cache-staleness)
  → final whole-branch review (opus) → Fable strategic sign-off → deployed (restart.sh, all 4 services clean) →
  smoke-tested (cyber security 13 all-correct, Oria/GSA regressions clean) → **MERGED to main via isolated worktree
  (main=612f1d7, area code byte-identical to deployed branch; PILOT STAYS UNMERGED) → PUSHED to origin/main.**
  Spec `docs/superpowers/specs/2026-07-07-llm-verified-area-expansion-design.md` (§9 REVISION v2 = binding reqs).
  Plan `docs/superpowers/plans/2026-07-07-llm-verified-area-expansion.md`. VRAM note: gemma 8GB+granite 4.2GB+
  embed on 16GB fits (~15GB, embedder briefly CPU-spills only while gemma resident post-area-query). Deployed code
  lives on branch feat/processing-debt-pilot @ 2954b38 (== main's area code); the running bots use it.

**🟢 INFRA CHANGE THIS SESSION (2026-07-07 PM) — VRAM/model diet (owner-driven, shared 16GB GPU w/ his ML research):**
  - Ollama now has ONLY 2 models: `granite4:tiny-h` (4.2GB, gen) + `qwen3-embedding:0.6b` (0.6GB, embed). Peak bot
    VRAM ~4.8GB, ~11GB free for research. Disk 17GB→4.6GB.
  - REMOVED: llama3.1:8b + nomic-embed-text (were installed, never loaded) — commit `7dc9e25` repointed every dead
    fallback default (config.py/embedder.py/ollama_client.py/model_descriptor DEFAULT_DESCRIPTOR) nomic→qwen so
    nothing calls them; NOMIC descriptor kept for legacy content_hash lookups; test flipped to defaults_to_qwen.
  - REMOVED: gemma3:12b (+ bake-off candidates gemma3:4b/qwen3:4b). **AREA_VERIFY_MODEL default gemma3:12b→
    granite4:tiny-h** (commit `dd3219e`) — REUSES the resident gen model, 0 extra VRAM. Gold-gate bake-off (55 pairs,
    prod chunk-of-10): granite **precision 0.846 / recall 0.815** vs gemma 0.923/0.889 — BELOW the 0.9 gate, an
    EXPLICIT owner-sanctioned trade ("even losing some answers/precision"); recall healthy (no collapse). Live-verified:
    "who works on cyber security" → 18 faculty (Fix #1 intact, slightly more inclusive). Spec R6 UPDATE #2 records it.
  - ✅ MERGED TO MAIN (2026-07-07): both infra commits cherry-picked onto main via isolated worktree →
    **origin/main = `e7ef2a4`** (612f1d7→ea2204e→e7ef2a4; 120 tests pass on main; pilot stays unmerged as always).
    main + deployed pilot branch now CONSISTENT (AREA_VERIFY_MODEL=granite4:tiny-h, qwen defaults). No loose end.
  - Re-gating to a stronger verify model later = pull it + set `AREA_VERIFY_MODEL` env (gate stays arbiter).

**⏭⏭ THE NEXT MOVE (Fable's #1, highest value): the KG+KB WINNER-TAKE-ALL fix.**
  For person/entity queries the router SHORT-CIRCUITS to the KG structured card (`message_handler.py:438` returns
  the entity_card verbatim) and NEVER consults KB prose → owned narrative facts are dropped. PROVEN for Vincent
  Oria (used_ai=False): the bot gives a complete structured card but drops his curated "Who is Prof. Vincent Oria?"
  bio FAQ (KB id=64, CE=1.000), his MMI-workshop involvement, the chair-welcome page. Affects EVERY person/org query
  with KB prose beyond the profile — plausibly a BIGGER lever than area-expansion (person-query traffic = the bot's
  center of gravity). DESIGN IT in the SAME proven pattern this session built: deterministic card stays VERBATIM +
  a gated, clearly-ATTRIBUTED prose ADDENDUM (think `deterministic_suffix`, not a free compose over merged context)
  — this avoids reopening the fabrication surface the verbatim-card guarantee exists to close. Interacts with WS4
  gate + the parked roster-hallucination warmth-vs-safety call ([[project_deterministic_rosters_fix]]). START:
  brainstorming skill → design spec → senior-eng + Fable review → TDD. Grab the trivial **R12 router one-liner**
  (loose-phrased umbrellas with zero literal tag match still route to RAG) alongside it.
  ALSO available anytime: a **$0 cached Set-A re-run** (oracle cache in eval/processing_debt/.cache/) to PROVE the
  debt number moved after fixes land — the instrument's best remaining use (don't drift back into κ/materiality
  polishing; the owner killed that).

**Other 86-miss clusters (lower confidence, revisit after the KG+KB lever):** prose semantic recall (halal food/
  library/cheap eats); vague contextless follow-ups (mostly NOT real debt); identity/general NJIT facts.

▼ prior build history (area-expansion, now COMPLETE — kept for provenance) ▼
**DECISION (owner, 2026-07-07): GOAL = BETTER BOT. STOP MEASURING. FIX THE RETRIEVAL POOL.** Measurement DONE,
κ/headline abandoned as not-the-deliverable. ~85% POOL-dominance = the settled finding.

**FIRST FIX CHOSEN (my call, owner delegated): RESEARCH-AREA UMBRELLA MATCHING.** Proven live bug:
"who is working on cyber security" → bot answers "1 faculty: Chase Wu" but we OWN 15 faculty whose areas
contain "security" (fragmented across 15 separate ResearchArea nodes — "cybersecurity" is even a DIFFERENT
node than "cyber security"). Router routes CORRECTLY to people_by_research_area(area='cyber security'); the
skill does FTS **exact-phrase** matching (skills.py `_research_entities`/`_fts_query`/`expand_area`, AREA_SYNONYMS
tight by design) → matches only the literal-phrase node → surfaces 1/15 (~93% owned experts dropped). GENERALIZES
to every umbrella topic (ML/AI/networks/databases). This is the flagship of the 86 POOL owned-misses.

**MECHANISM (verified in code):** expand_area("cyber security")→["cyber security"] (not in AREA_SYNONYMS)
→ _fts_query quotes it as one FTS phrase → _research_entities MATCHes only exact-phrase → 1 person. The 15
security faculty carry subtype tags (network/cloud/system/wireless/mobile security, "cybersecurity" one-word).

**PHASE NOW: SPEC WRITTEN + REVIEWS DISPATCHED (awaiting findings → owner sign-off → TDD build).**
  Approach CHOSEN by owner: **LLM-VERIFY area expansion** (after he rejected hardcoded allowlist as "hardcoding,
  find a better way"). Ruled out en route (both measured, not guessed): (a) token/head-noun broadening needs a
  hardcoded safe-head allowlist (systems→59 tags, networks→neural+social, learning→motor/service = over-match);
  (b) bare embedding cosine threshold — real sibling operating↔distributed systems=0.40 scores LOWER than false
  friend computer↔neural networks=0.56, so NO single cutoff separates classes. LLM-verify = embed query → KNN
  top-30 shortlist of owned area tags → LLM picks same-field subset (constrained to owned tags, can't hallucinate)
  → existing deterministic SQL. Fail-safe to exact-match. Live per-query + persistent cache. Enumerate skills only
  (does_person_research_area stays exact). Verify-model = config (granite4:tiny-h flagged too weak per judge work
  → recommend llama3.1:8b + eval-validate).
  SPEC: `docs/superpowers/specs/2026-07-07-llm-verified-area-expansion-design.md` (committed **0ad6d98** on
  feat/processing-debt-pilot). REVIEWS DISPATCHED (background agents): Fable(RAG/LLM) + senior-eng, both reviewing
  spec vs code + against the §8 goals checklist. ⏭ NEXT: collect both reviews → relay to owner → his sign-off (or
  Fable stands in per delegate-to-Fable) → TDD build (new module v2/core/retrieval/area_expand.py + hook in
  people_by_research_area) → show diff → commit + restart. DB-only? No — code change, needs restart.
**GATE (owner RELAXED it 2026-07-07): NO per-step owner sign-off needed for the area-expansion fix.** Owner said
  "no need my sign off after fable get the review, I am ok to continue without me." So: wait for Fable's RAG
  review → address BOTH reviews' conditions in the spec → if Fable approves, PROCEED autopilot (TDD build →
  verify → commit + restart) WITHOUT pinging for sign-off. Fable's OK stands in per delegate-to-Fable. Still SHOW
  the diff/outcomes as I go (owner can steer), just don't block. Branch: feat/processing-debt-pilot or fresh
  feat/area-expansion — my call at build.
**BOTH REVIEWS DONE (approve-with-conditions) → SPEC REVISION v2 COMMITTED `325a27a`. NOW: plan → TDD build (autopilot, no sign-off).**
  Spec §9 REVISION v2 resolves every finding. KEY resolutions to BUILD to: R1 `_research_entities(...,expand=False)`
  param; enumerate skills pass expand=True (list==count structural); yes/no keeps expand=False for "yes". R2 (Fable's
  deep catch) NEW `"related"` verdict in does_person_research_area: exact-no BUT holds verified sibling tag → honest-
  partial "lists system security, a form of security; not 'cyber security' as such" — NEVER false "no" (was skills.py:353).
  R3 cache key=(norm_area,vocab_hash,model_id,prompt_version,top_k). R4 answer wording annotates each name w/ its OWN
  verified tag ("Neamtiu (system security)") — anti-fab. R5 candidates = KNN top-30 ∪ token-overlap (recall-only, LLM
  prunes). R6 seam=generate_json_sync (ollama_client.py:21, SYNC) + JSON schema {"indices":[int]} + AREA_VERIFY_MODEL
  partial (default llama3.1:8b). R7 few-shot DIRECTIONAL-NEGATIVE prompt + ~50-pair gold gate precision≥0.9 (re-pass on
  model swap). R8 AREA_EXPAND_ENABLED flag + structured logging (separate LLM-error from legit-none). R9 cache+vocab-embed
  in OPS DB via SELF-OWNED writable conn (never passed conn), INSERT OR REPLACE+WAL, vocab_hash recompute gated by cheap
  MAX(rowid)/COUNT change-detector + in-proc memo. R10 Qwen asymmetric: query→embed_query, tags→embed_document, L2-norm;
  canonicalize query via AREA_SYNONYMS before embed. R11 verify timeout↑ (~20s) / keep_alive prewarm vs llama cold-load;
  DI injectable embedder+verify. R12 DEFERRED: loose-router-gate zero-literal-tag umbrellas still →RAG. Module=
  v2/core/retrieval/area_expand.py. ↓ prior senior-eng detail below (superseded by v2 above) ↓
**SENIOR-ENG REVIEW DONE (approve-with-conditions).** Blocking F1 (real catch): `_research_entities` (skills.py:297)
  is shared by THREE callers incl. does_person_research_area (:347), so "enumerate-only expansion + yes/no stays
  exact" collides with "list==count via shared fn". FIX: keep _research_entities EXACT (yes/no unchanged); add
  `_research_entities_expanded = exact ∪ verified-tag people`; BOTH people_by_research_area + count call THAT;
  update does_person_research_area docstring to accept list⊋yes/no (relaxed, explicitly). Should-fix: F2 use
  module-level `generate_json_sync` (ollama_client.py:21, SYNC — router path is sync, no asyncio.run) + JSON
  schema {"indices":[int]} + dedicated `AREA_VERIFY_MODEL` partial (default llama3.1:8b, INSTALLED verified);
  inject as callable for stubbable tests (mirror assistant.py:138-139 gen_json/embedder DI). F3 cache+vocab-embed
  in OPS DB (get_ops_connection/OPS_DB_PATH schema.py:30) via SELF-OWNED short-lived writable conn — NEVER the
  passed conn (graph-write invariant); INSERT OR REPLACE + WAL multi-process-safe; REJECT JSON file (3 bot procs
  race). F4 Qwen asymmetric: query→embed_query, 1379 tags→embed_document, both L2-norm (embedder.py:72/75; do NOT
  use private _embed_batch :43 = no prefix/no norm). F5 add structured logging (fail-safe degrades SILENTLY — can't
  tell if fix fires). F6 timeout 6.0s default vs llama cold-load → first post-restart query may fall back; raise
  timeout / keep_alive pre-warm / document; note VRAM (granite+llama+embedder resident). F7 DI in signatures.
  1379 distinct tags CONFIRMED. §8 goals: all core supported once F1 wrapper-split resolves the one self-contradiction.
**2ND LEVER FOUND THIS SESSION (parked, maybe bigger): KG+KB winner-take-all short-circuit.** message_handler.py
  :438 — when UnifiedRouter fires a structured skill (entity_card) it returns the KG card VERBATIM and NEVER runs
  semantic RAG. Proven for Oria (used_ai=False): bot gives complete structured card but DROPS owned KB prose (his
  curated "Who is Prof. Vincent Oria?" FAQ bio id=64 CE=1.000, MMI-workshop involvement, chair-welcome page).
  Owner's idea = gather KG+KB, decide what to bring back. Real design tension: structured path = anti-fabrication/
  verbatim guarantee; merging reopens compose/fabrication surface (like deterministic_suffix + WS4 gate shape).
  Affects EVERY person/org query with KB prose beyond the profile. DEFERRED behind the area fix per owner.
**Other 86-miss clusters (deferred, lower confidence):** prose semantic recall (halal food/library/cheap eats);
  vague contextless follow-ups ("who do I contact about this?" ×12, mostly NOT real debt); identity/general NJIT
  facts (Persian "who are you", "founded 1885" — questionable ownership/materiality). Revisit AFTER the first win.

▼ prior resume block (measurement era — SUPERSEDED by the decision above, kept for provenance) ▼
**Owner paused here on purpose.** He wants a fresh session to resume at this precise moment. DON'T
re-derive; DON'T blind-launch a re-run (each is ~2hr). Read this block, then re-present THE PENDING
DECISION below (he rejected my 3-option framing wanting to clarify first — ASK what he wants to clarify,
then reframe). Context hit ~80% last session = why we stopped.

**GIT (all durable, committed):** on branch `feat/processing-debt-pilot`, HEAD = **f75b5d5**. Three judge-fix
commits this session, all UNMERGED (pilot stays unmerged per plan):
  bcf2cc0 judge fix (NLI-primary presence + confident lean + pronoun bucket) · 3ed6a70 scope+window guard &
  IN_ANSWER · f75b5d5 guard windows FULL page (not page[:8000]). 139 tests pass. main=d462d7e (FacultyFolio
  work landed there this session — see git-reconciliation note; that's DONE, unrelated).

**WHAT'S BUILT & WORKING (the instrument is fully functional):** NLI cross-encoder judge (Xenova/nli-deberta-v3-base,
models/nli/, ONNX/reranker pattern, sub-batch 16, calibrated P(entail), asymmetry-pinned direction). Presence:
confident-yes-only lean + low_conf band + rare-term windowing (B1 hard-slice PASSED: KG-blob concern DISPROVEN,
NLI entails node cards 0.99). Pronoun/non-self-contained bucket. Guard + IN_ANSWER now WINDOWED (were the bug).
Report: buckets + threshold-sensitivity + §3.2-reversal flag. Adjudicate CSV: audit cols. FAIL-LOUD (never
silent-granite). All Fable-signed-off (design + impl + the guard-scope opinion).

**THE STORY (3 re-runs, 3 SC2 fails, each caught a REAL bug — instrument self-validation working):**
  Set A granite baseline: 74% debt (inflated 2× by weak granite judge + unsure→present lean).
  Re-run #1 (NLI, bcf2cc0): 92% — WORSE. Controls caught it: NLI default also hit oracle_guard + IN_ANSWER
    (shared entailment.entails), unwindowed → over-strict, dropped "Oria is chair".
  Re-run #2 (3ed6a70, guard lenient + windowed): 88%, STILL SC2-fail. Root cause = guard page[:8000] truncation
    (Oria text at char 11,014). FIXED f75b5d5.
  Control re-check (fixed, validate_controls.py): "Oria is Chair" now IN_ANSWER ✓ BUT pos-control debt STILL 55%
    — NOW for a DIFFERENT reason: **MATERIALITY** (oracle volunteers TANGENTIAL owned facts, e.g. Shantanu's job
    TITLE on a "what does he research" Q, CS's YWCC parentage on a "who is chair" Q). That's "not responsive",
    not "failed to surface". NOT a bug — a metric-scoping issue.

**🟢 THE ROBUST FINDING (unchanged across ALL 3 runs, the thing that MATTERS):** debt is REAL and
  **~85% POOL-dominated** (facts we own that never enter the retrieval candidate pool). Fable PROVED it survives
  every bug (guard-dropped facts were 96% POOL). **The actionable direction — FIX RETRIEVAL POOL — is SETTLED
  regardless of the exact headline %.** The GOAL is a better bot (owner reaffirmed); κ/headline is the trust-gate,
  not the deliverable.

**💡 OWNER'S KEY INSIGHT (the cleaner fix): "we have Vincent Oria on KG right?"** YES. The guard is an
  ORACLE-hallucination filter (does Brave's citation back its own claim), NOT a we-own filter — but it runs FIRST
  and drops facts we DEMONSTRABLY own. PRINCIPLE: a fact we can independently verify we OWN can't be vetoed by the
  oracle's citation. DESIGN = OWNERSHIP-FIRST reorder in classify.py: if presence confirms ownership → measure it
  (IN_ANSWER/OWNED_NOT_SURFACED), ignore guard; guard only for NOT-owned facts (real-gap vs oracle-halluc).

**⏭ THE PENDING DECISION (unresolved — resolve FIRST next session):** three paths —
  (1) **Take POOL finding, pivot to fixing retrieval** [my lean: diminishing returns on the exact %, POOL is
      actionable now, GOAL=better bot]. (2) **Keep refining instrument**: add MATERIALITY gate (count only
      vital-AND-responsive facts) + OWNERSHIP-FIRST reorder → Fable review → another ~2hr re-run → trustworthy
      headline + κ. (3) already executed = checkpoint & fresh session (this). Owner leaning unclear — ASK.

**ARTIFACTS (all on disk):** out/facts_A_granite.jsonl (74% baseline) · facts_A_nli_bugged.jsonl (re-run#1) ·
  facts_A.jsonl (re-run#2, 88%) · facts_CTL.jsonl (control re-check) · adjudicate_A.csv (342 rows, ready for
  human labeling IF we go the κ route) · out/pilot_report_A.md. Scratchpad: judge_bakeoff.py, nli_bakeoff.py,
  b1_hard_slice.py, run_setA_nli.py (re-run harness, resume=False re-classifies, $0 cached oracle),
  setA_gate_report.py (SC2-gate-then-report), validate_controls.py (fast 8-control SC2 check ~5-8min).
  Oracle cache: eval/processing_debt/.cache/oracle/ (50 answers → re-runs are $0). Env: BRAVE_ANSWERS_API_KEY_2
  (Hamideh) added for failover (dormant). A full re-run ≈ 2hr (ask.sh per Q is the slow pole, not NLI).

**IF (2) chosen — build notes:** materiality = nuggetize.py vital/okay gate is too loose (marks tangential facts
  vital); tighten to question-responsive. ownership-first = reorder classify.classify_fact (presence before/instead-of
  guard for owned facts). Both = Fable-gated design changes. Then re-run (validate_controls.py FIRST = fast SC2
  check before the 2hr full run). Pre-registered gate: pos-control debt ≤~20% + Oria=IN_ANSWER (already ✓).
═══════════════════════════════════════════════════════════════════════════════════════════════

## Status @ 2026-07-07 (PM) — JUDGE FIX: GATE PASSED, BUILDING (superseded by RESUME block above)
**Phase:** Judge-fix design DONE + reviewed + **Fable APPROVED-TO-BUILD** (Fable = delegated approval judge per owner).
Bake-off proved it (11 real Set-A pairs, `scratchpad/judge_bakeoff.py`+`nli_bakeoff.py`): granite 1/7 reject
(6/7 bug), NLI/llama/gemma all 7/7 reject + 4/4 keep; **NLI deberta-v3 = calibrated (entail 0.00 vs 0.86-1.00),
33ms/pair (20-40× faster)**. Chosen judge = **NLI deberta-v3 primary** (Xenova/nli-deberta-v3-base, ONNX via
reranker pattern, already in `models/nli/`), gemma3:12b escalation **CUT for pilot** (PD_ESCALATE=off).
Design: `2026-07-07-judge-fix-delta-design.md`. Reviews: `2026-07-07-judge-fix-reviews.md` (senior-eng + RAG).
Fable gate (full text in that session): **APPROVED w/ 4 blocking conditions**:
  B4 pin premise=span/hypothesis=fact + asymmetry test (BUILD FIRST).
  B2 surface low-conf band [LO=0.35,HI=0.5) END-TO-END (PresenceResult.low_conf → classify flag → report bucket
     + adjudication CSV rows). NOT_OWNED-classed but MUST reach human = the one true κ threat.
  B3 window long fts/embed spans to match-neighborhood before NLI (512-trunc → false NOT_OWNED). batched.
  B1 HARD-SLICE GATE (zero spend, before adjudication): ~20-30 genuine owned-misses from facts_A.jsonl (KG
     attr-blobs + long pages) must stay ≥HI after B3. IF KG spans fail → verbalize node spans to sentences.
  Pronoun ruling = mechanical EXCLUDE+BUCKET dangling-opener nuggets (no proper noun) from κ denom + headline,
  report as "non-self-contained" bucket (full nuggetizer resolution deferred). Escalation CUT (PD_ESCALATE=off).
  Fold-ins: IMPROVED_SYSTEM prompt for ALL generative backends; FAIL LOUD (never silent-fallback to granite =
  JUDGE_ERROR/abort); record judge-id + max P(entail) per FactRecord+CSV; sub-batch ~32; read PD_JUDGE once;
  update inverted lean test; rebuild run harness. Rejected-as-blocking: held-out threshold calibration (pre-reg
  HI=0.5 + report sensitivity at HI∈{0.4,0.5,0.6}; do NOT tune on κ labels = circularity).
  Re-run MUST report: confident-only headline+CI, low-conf bucket outcome, non-self-contained count, κ over
  judgeable only (SC1≥0.6), sensitivity bands (off-topic + HI sweep), IN_ANSWER/OWNED/NOT_OWNED deltas vs
  granite run (decompose judge vs lean vs recount), POOL-dominance survives, hard-slice bound, §3.2 reversal flag.
**Build order:** B4 → nli_judge+batching → B2 → B3 → pronoun bucket → B1 hard-slice → re-run Set A (CACHED
oracle, ~$0 Brave) → adjudicate → κ. All on branch feat/processing-debt-pilot (unmerged).

### ✅ BUILD COMPLETE @ 2026-07-07 (PM) — 131 tests pass, B1 GATE PASS. Awaiting Fable impl sign-off → commit → re-run.
Files: NEW `nli_judge.py` (Xenova/nli-deberta-v3-base, ONNX/reranker pattern, sub-batch 16, entail-idx from
config, fail→None), NEW `self_contained.py` (pronoun gate). CHANGED `entailment.py` (PD_JUDGE env-select
default nli; score_to_verdict HI=.5/LO=.35; batch_verdicts FAIL-LOUD on None; IMPROVED_SYSTEM for generative;
active_judge_id), `presence_check.py` (batch+windowed NLI; NEW lean present=confident-yes-only, low_conf band
retained; `_nli_windows` rare-term localization), `classify.py` (NON_SELF_CONTAINED gate first; judge_id+max_score
audit), `types.py` (PresenceResult.low_conf/max_score; FactRecord.judge_id/max_score; NON_SELF_CONTAINED class),
`report.py` (debt_at_threshold sensitivity 0.4/0.5/0.6; low_conf+non_self_contained+not_owned buckets; §3.2
reversal flag rendered), `adjudicate.py` (audit cols machine_low_conf/max_score/judge_id/probes). Tests: +test_nli_judge
(6, asymmetry pins direction B4), +test_entailment_judge (12), +test_self_contained (7), updated presence/classify/
report/adjudicate. Memory-OOM fix = sub-batch (a 500-row seq-512 batch blew >27GB).
🟢 **B1 HARD-SLICE GATE = PASS** (`scratchpad/b1_hard_slice.py`, 18 genuine owned-misses from facts_A.jsonl, live
DB, real NLI): KG-NODE 4/4 (100% — the pipe-joined node-blob concern is DISPROVEN, NLI entails at 0.99), LONG 5/7
(71%), SHORT 7/7. The 2 LONG "absent": (1) student-count fact = GENUINE partial-ownership (corpus has "12,332
students" but NOT "92 countries"/"fall 2022" → correct NOT_OWNED, not a bug); (2) NJIT-ID = minor windowing miss
(corpus has near-verbatim). Under-report bound ≈ 1/18 genuine. Report this as the instrument's under-report bound.
🟢 **Re-run = $0 Brave**: eval/processing_debt/.cache/oracle/ has all 50 cached answers; ask_oracle hits disk.
Re-run mechanics: run with resume=False (re-classify ALL facts w/ NLI) — BUT first `cp facts_A.jsonl
facts_A_granite.jsonl` (+ report/csv) to preserve the granite run for the decomposition-vs-granite delta (Fable req).
### ✅ JUDGE FIX COMMITTED `bcf2cc0` (Fable impl sign-off passed). Then RE-RUN #1 exposed a 2nd bug → fixed `3ed6a70`.
RE-RUN #1 (NLI, commit bcf2cc0) headline read 92% (WORSE) — but the POSITIVE CONTROLS caught a confound:
the NLI judge default silently changed TWO more decisions that reuse `entailment.entails` — the
**oracle_guard** (`oracle_guard.py:26,32`) and the **IN_ANSWER** check (`classify.py`). Neither was windowed →
under NLI's 512-tok cap the guard degraded to snippet/head-only and OVER-DROPPED: "Vincent Oria is the Chair"
(a positive-control's literal answer) got DROPPED_ORACLE; IN_ANSWER collapsed 43→6. Debt inflated because
answerable facts left the denominator. Same B3 truncation bug, 2 more call sites.
**Fable opinion (delegated sign-off): Option A — scope+window guard & IN_ANSWER, then adjudicate.** Proven by
Fable's cross-tab: guard-dropped facts were 96% POOL in granite → re-admitting STRENGTHENS pool-dominance to
~89%. **The actionable direction (fix retrieval POOL) is SETTLED regardless of the re-run** — re-run is only
for a trustable headline + κ. Guard must be LENIENT (false-drop breaks controls; false-keep→NOT_OWNED harmless).
FIX `3ed6a70` (138 tests): entailment.text_entails_fact (IN_ANSWER windowed strict-yes≥HI) + supported_by
(GUARD windowed LENIENT, keep unless clearly-no P≥GUARD_LO=0.35, env PD_GUARD_LO); oracle_guard→supported_by;
classify IN_ANSWER→text_entails_fact; _nli_windows no-anchor now covers WHOLE span (12 windows, was head-only);
Oria regression test (real NLI 0.997 keeps it). Baselines preserved: facts_A_granite.jsonl, facts_A_nli_bugged.jsonl.
### 🔄 RE-RUN #2 RUNNING (commit 3ed6a70, $0 cached oracle, ~2hr, scratchpad/run_setA_nli.py + log run_setA_nli_v2.log)
POST-RUN: `scratchpad/setA_gate_report.py` checks PRE-REGISTERED SC2 GATE FIRST (positive-control debt ≤20% +
Oria=IN_ANSWER) BEFORE headline (Fable: don't tune to headline); if PASS → render report + emit adjudicate_A.csv.
NEXT: (a) SC2 gate check. (b) IF PASS → hand owner adjudicate_A.csv (100% human label = κ, SC1≥0.6). IF FAIL →
diagnose, don't trust headline. (c) Also eyeball the 18 granite-IN_ANSWER→NLI-flip facts as a cheap acceptance check.
### RE-RUN #2 (commit 3ed6a70) SC2 gate = FAIL AGAIN → 3rd root cause found + fixed (NOT yet committed)
Re-run #2 headline 88%, but SC2 FAILED: positive-control debt 50%, "Oria is Chair" STILL DROPPED_ORACLE.
ROOT CAUSE #3 (diagnosed, dispositive): guard's `oracle_guard.py:32` passed `page[:8000]` — but on the real
cs.njit.edu/welcome-chairperson page "Vincent Oria \nChair, Computer Science Department" sits at char 11,014,
PAST the 8000 cut, BEFORE windowing. Proof: supported_by(fact, page[:8000])=0.04 MISS vs supported_by(fact,
page)=0.99 KEEP. The [:8000] cap predates windowing = dead weight (windowing bounds the work). Also: Brave
citation SNIPPETS are lossy elliptical fragments — NONE of the 4 for the CS-chair Q contains "Oria is chair"
(cite[2] is about Jamie Payton!) — so the guard MUST rely on the page-fetch path, which the truncation broke.
FIX (uncommitted): oracle_guard.py `page[:8000]`→`page` + regression test (marker past char 8000 kept). Guard
tests 5 green. NOTE: terse signature "Vincent Oria\nChair, CS Dept" ALONE scores 0.0005 but 0.99 in fuller
window — NLI weak on isolated terse structured text, rescued by full-page windowing.
### 🔄 VALIDATING (scratchpad/validate_controls.py, log validate_controls.log): fast SC2 re-check on the 8
CONTROLS ONLY (cached oracle, ask.sh × 8 ~5-8min) before committing to another 2hr full re-run. IF controls
pass (pos-ctrl debt ≤20% + Oria=IN_ANSWER) → commit guard fix + full re-run. IF still fail → guard design
needs rethink (Brave snippets lossy + page-fetch fragile; maybe loop Fable).
### ROBUST ACROSS ALL 3 ITERATIONS: POOL-dominance ~85% (62/73 owned-misses re-run #2). The DIRECTION (fix
retrieval POOL) is settled; only the exact denominator (guard drops + IN_ANSWER count) keeps needing scoping.
This is iteration 3 of whack-a-mole on the shared entailment-driven decisions (presence✓ → guard-snippet →
guard-page-trunc). Each is the SAME B3 512-truncation bug on a new call site. Context ~76% this session.
### 💡 OWNER INSIGHT (2026-07-07) = the CLEANER FIX (better than truncation patching): "we have Vincent Oria
on KG right?" YES (presence 0.99). The guard is an ORACLE-hallucination filter (does Brave's citation back its
own claim), NOT a we-own filter — but it runs FIRST and drops facts we DEMONSTRABLY own before presence checks.
PRINCIPLE: a fact we can independently verify we OWN is not a hallucination, so the oracle's citation quality
must not veto it. DESIGN = OWNERSHIP-FIRST reorder: if presence confirms ownership → measure it (IN_ANSWER/
OWNED_NOT_SURFACED), IGNORE guard; fall back to guard ONLY for NOT-owned facts (real-gap vs oracle-halluc).
Eliminates the whole guard-truncation class for KG/corpus facts. STRUCTURAL change (classify.py order) → needs
Fable review. Two paths pending owner: (1) ship truncation fix only [minimal, control-run tests it], (2) reorder
ownership-first [better, Fable-gated]. LEANING (2) but confirm after control-run result.
↓ prior status below ↓

## Status @ 2026-07-06  (SESSION LIMIT — resets 8pm America/New_York)
**Phase:** PLAN WRITTEN. Owner APPROVED design + CHOSE **subagent-driven** build. Reviews: 1 done, 1 must re-run.
No code written. $0 spent.

### Review outcomes
1. ✅ **Fable ruled on the deviation → KEEP-DEVIATION** (roll our own Granite entailment/nuggetize, do NOT
   add RAGChecker pip). Reason: RAGChecker's published human-correlation is measured with THEIR judges
   (GPT-4/Llama3-70B); swapping in granite4:tiny-h means we DON'T inherit their validation anyway — so the
   pip buys only the *protocol*, which the local build already gives us. The pilot validates OUR judge's κ
   vs human labels directly = strictly stronger than borrowed validation. Keep interface swappable for scale.
   A weak Granite judge can only LOWER κ (a correct finding / SC1 blocks scale), it cannot corrupt
   conclusions since we 100%-adjudicate. **TWO GUARDRAILS TO FOLD INTO THE PLAN (not yet folded):**
   - **Guardrail A (important): human-validate the NUGGET SET, not just entailment.** κ on entailment is
     BLIND to decompose errors (dropped/over-split/non-atomic facts). In the adjudication CSV/step (T4,
     T11, T15), have the human also accept/reject/add-missing per question's nugget list → yields a
     decompose-quality (precision/recall) number alongside κ. Decompose is where a tiny model fails.
   - **Guardrail B: entailment judge = THREE-way (yes/no/UNSURE), not forced binary** (T3 `entailment.py`
     `_SCHEMA`/return). Route `unsure` to priority human review. Skip self-consistency voting (needs temp>0,
     pointless when 100%-adjudicating). Do NOT add a second stronger decompose model preemptively — let
     Guardrail A's number decide that for the scale phase.
2. ✅ **Senior-eng plan review DONE** (rerun after limit reset) → verdict **BUILD AFTER MUST-FIXES**. Full
   review saved: `2026-07-06-senior-eng-review-impl-plan.md`. Found REAL crux bugs that UNDER-report debt.

### MUST-FIX before build (fold into plan — see review file for detail + file/line evidence)
  M1 kg_probe span=bare name → build span from name+attrs+edges (else KG attr facts wrongly NOT_OWNED). [T6]
  M2 embed_probe dead: (a) Embedder from active_descriptor (Qwen 1024-d, assert len==dim); (b) prod KNN SQL
     `embedding MATCH ? ORDER BY distance LIMIT ?` + sqlite_vec.serialize_float32, NO `k=?`. Live assert in T14. [T6]
  M3 grep over content+title+nodes.attrs (not content only); span=DB window not fact-fragment; fts span not [:300]. [T6]
  M4 exclude_types: READ live `retriever.exclude_types` (real default {publication,syllabus}, admin-tunable);
     hardcoded {"publication"} misses syllabus → CONFIG mis-attribution. [T9]
  M5 SC6 oracle-correctness rate: compute (DROPPED_ORACLE+we_are_authority)/guarded, render, >30% gate. [T9/T13]
  M6 Brave ANSWERS entitlement = HARD GATE blocking T15 (we already verified brave-pro works live, keep gate). [T2/T14]
### SHOULD-FIX: drop Route.family (Route=Route(skill,args)); real read-only conn (file:...?mode=ro); capture
  answers with LIVE_ENABLED=0 (else live-fallback = uncounted Brave Search spend + confounds IN_ANSWER);
  xray align to PROD retrieval cfg (group_by_entity=True + deep-fallback + office tier, pool limit≥2×pool_size);
  ROUTER branch check OWNING skill not just any-node; sampler dedupe/cluster; strip answer delimiter; hard
  ≤50 spend counter; PII-scan sample before outbound.
### Guardrail folding (harder than thought):
  - Fable-A (validate nugget SET) CONFLICTS with positional κ → redesign adjudicate.py to key facts by
    hash(question+fact_text), join machine/human by KEY not position.
  - Fable-B (three-way yes/no/UNSURE) touches 4 bool sites; UNSURE leans OPPOSITE: IN_ANSWER→not-in-answer,
    PRESENCE→lean PRESENT (else understates debt).

### ✅ FOLD DONE — Plan "REVISION v2" appended to the plan file (supersedes where noted)
Owner decision: **3 sets × 50 = 150 questions** (Set A real logs `questions` table / Set B SampleQuestions-DB
/ Set C SampleQuestions-web). Cost ~$8.55 (~$3.55 after free credits, or 2nd free Answers key — owner OK).
Adjudication ~600 nuggets ≈ 3 sessions (owner OK). Revision v2 folds: R0 sourcing · R1 multi-key+spend guard ·
R2 real read-only conn · R3 LIVE_ENABLED=0 capture · R4 three-way entailment (per-caller UNSURE lean:
IN_ANSWER→no, PRESENCE→present, guard→drop, eRAG→false) · R5 presence M1/M2/M3 (kg span from name+attrs+edges;
embed from active_descriptor + prod KNN SQL; grep content+title+attrs) · R6 M4 live exclude_types + ROUTER
precision · R7 xray prod-fidelity · R8 adjudicate KEY-based + nugget-set precision/recall · R9 SC6 + per-set
reports · R10 control gate = real halt · R12 build order (Set A first, stop if SC1 κ fails).

### R13 ADDED: power-analysis in report.py — cluster-bootstrap 95% CI on debt (resample over QUESTIONS,
not nuggets, since nuggets are clustered) + required-N back-solve (n=z²p(1-p)/E² → facts→questions, overall
+ per-stage). Makes the pilot self-answering on sample size. 150 confirmed enough for the κ gate (~600
decisions > ARES ~300) + solid overall debt CI (±~5%); per-stage will be wide → power analysis quantifies it.

### ✅ FABLE FINAL CHECK = GO (folded as R14: B1-B5 + unsure-rate). Owner said "free to go" → BUILDING.
  B1 guard-drop recoverable (human_guard_ok col) · B2 control gate on HUMAN-confirmed misses (adjudicate
  controls FIRST) · B3 required_n cluster-consistent (DEFF/ICC) · B4 suppress Set-C low-denom debt headline ·
  B5 verify RO-handle compat in T14 · minor: report unsure-rate. B1/B2/B4/B5 gate SPENDING (not build start).

### NEXT ACTIONS — BUILDING NOW (subagent-driven)
  a. ▶ superpowers:subagent-driven-development — fresh subagent per task, Tasks 1→13 as AMENDED by
     REVISION v2 (R0–R14). All $0/read-only. Review between tasks.
  b. T14 = SPEND GATE (real halt): Brave-200 probe + B5 RO-check + adjudicate controls → SC2/SC3 before paid run.
  c. Set A first (real logs). Stop if SC1 κ fails; else B, C.
  ═══ ▶▶ NEXT SESSION START HERE (owner cue: "processing debt") — DRIVE THE JUDGE FIX ═══
  On the cue, DON'T re-derive — the design is already worked out below. Present it, get the 2 owner choices, then BUILD (TDD + review) and RE-RUN Set A.
  THE FIX (make the presence/IN_ANSWER entailment judge trustworthy — root cause of the 2× inflation):
   (1) MODEL: entailment.py hardcodes model="granite4:tiny-h" (line 13). Make it configurable via env (LLM-agnostic HARD rule). TESTED on the real failure cases: granite-tiny 3/5 (hedges "unsure" on unrelated pairs = the bug); llama3.1:8b 4/5 (says "no" correctly; 5th was cold-load timeout). llama3.1:8b IS INSTALLED.
   (2) PROMPT: current prompt lets "unsure" be a lazy default. New prompt: "answer 'no' when TEXT is about a DIFFERENT subject; 'unsure' ONLY for same-subject ambiguity." (Improved even granite in the test.)
   (3) LEAN: reconsider R4 "unsure→present" (it caused 76% of owned-misses to rest on 'unsure'). Options: require "yes" for the debt headline; or unsure+lexical-overlap; or bucket unsure-only separately (low-confidence).
   (4) RE-RUN Set A on the CACHED sample (oracle answers cached in .cache/oracle/ → ~no new Brave spend; resume=True). Then adjudicate → real κ.
  QUESTIONS TO ASK THE OWNER (the 2 real decisions):
   Q1: Judge model — (a) llama3.1:8b now [zero download, proven, but SLOW: ~4hr+ run], or (b) dedicated NLI cross-encoder (DeBERTa-v3-mnli, e.g. MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli or cross-encoder/nli-deberta-v3-base) [~440MB download, most accurate+CALIBRATED, 10-100× FASTER → also fixes the 4hr runtime; fits the existing ONNX reranker infra], or (c) qwen2.5:14b [strongest LLM judgment, fits 16GB, slower]. RECOMMEND (b) NLI as the judge, llama3.1 as escalation for borderline.
   Q2: Unsure lean — require "yes" for the headline debt, or keep unsure-as-present but report confident-only alongside?
  SYSTEM INVENTORY (verified, don't re-check): Ollama has granite4:tiny-h, llama3.1:8b, qwen3-embedding:0.6b, nomic. GPU RTX 4070 Ti SUPER 16GB (runs 8B easily, 14B q4 fits). Reranker already runs cross-encoders via ONNX (Xenova/ms-marco-MiniLM-L-6-v2, CPU provider) → NLI cross-encoder drops into same infra.
  AFTER THE FIX: re-run Set A → adjudicate (machine vs careful human) → κ (SC1 go/no-go) → if κ≥0.6, trust the number + decide Sets B/C. Instrument on branch feat/processing-debt-pilot (NOT merged; judge fix lands here first).
  ═══════════════════════════════════════════════════════════════════════════════════════

  ═══ RESULT STATE (2026-07-07) — SET A DONE, INSTRUMENT VALIDATION = FAIL (fix before scaling) ═══
  🔴 KEY FINDING (full writeup: `docs/research/oracle-processing-debt/2026-07-07-setA-validation-findings.md`):
  Set A ran (50 real-log Qs, 4.2hr, ~$2.85). Machine debt = 73.8% (POOL-dominated 94%). BUT that number is INFLATED ~2× and is NOT trustworthy. Three artifacts: (1) the granite4:tiny-h entailment judge is too weak — returns "unsure" for most fact–span pairs incl. unrelated ones (25 owned-misses re-judged: 5 yes / 19 unsure / 1 no); (2) the R4 "unsure→present" lean turns that hedging into false ownership → 76% of owned-misses rest ONLY on "unsure"; (3) real-log noise (off-topic/meta Qs + dangling-pronoun nuggets). Confident-"yes"-only debt ≈ ~35-40% (rough) — still real + POOL-dominated, but far below 74%.
  SC1 (κ go/no-go) = FAIL in spirit → DO NOT scale to Sets B/C. The pilot SUCCEEDED at its real job: it proved the naive number is 2× wrong + localized why, for ~$3.5, before we shipped a wrong 74%.
  ▶ FIX BEFORE RE-RUN (priority): (1) stronger entailment judge (swap granite-tiny → llama3.1:8b / Granite-gen / NLI model — LLM-agnostic config swap; BIGGEST lever); (2) reconsider unsure→present lean (require "yes", or unsure+lexical-overlap, or separate low-conf bucket); (3) self-contained nuggets (resolve pronouns, atomic claims); (4) topicality gate on real-log sampling (off-topic/meta Qs out of the debt denominator).
  Artifacts: out/facts_A.jsonl (339 facts), out/pilot_report_A.md, out/adjudicate_A.csv (222 vital rows), out/facts_controls.jsonl. Set A resumable runner: scratchpad/run_setA.py.
  Instrument code: all T1-T14 + 4 fixes reviewed clean, 93 tests, branch feat/processing-debt-pilot (NOT merged — hold; the judge/lean fixes come next). Controls #2 (28 supported, 14 POOL owned-misses) showed the mechanism works when the fact is genuinely owned.
  OWNER DECISION PENDING: fix the judge+lean and re-run Set A (recommended — cheap, the sample is already drawn/cached), or discuss the fix design first.
  ═══════════════════════════════════════════
  BUILD PROGRESS: T1-T14(no-spend) ✅ (ad71c60, 86 tests) reviews clean — INSTRUMENT FULLY BUILT + PRIMED. Only T15 (the actual run) remains, and it is 100% spend + owner adjudication.
  🟢 B5 RESULT: ro handle is genuinely read-only (route+retriever+vec/FTS MATCH all run under mode=ro, no incidental write). B5 CAUGHT A REAL BUG before spend: the pilot's IN-PROCESS xray/presence embedding defaulted to nomic-768 vs the live 1024-d Qwen corpus (active_descriptor() is env-selected via EMBEDDING_MODEL, unset in a bare shell). Without .env, presence embed_probe SILENTLY returns [] → garbage debt. FIXED: bootstrap.load_project_env() loads .env at import of run_pilot.py + pathlabel.py; gate.verify_embedding_alignment() halts LOUD if the KNN probe returns 0 hits. Amendment .superpowers/sdd/task-14-amendment.md.
  🔴🔴 T15 CONTROLS-FIRST BLOCKER (2026-07-06, ~$0.63 spent — controls-first WORKED, caught this before the $8.55 run):
  The Brave ANSWERS endpoint (res/v1/chat/completions) returns ZERO citations — raw response is a plain OpenAI chat completion (choices[0].message.content only; no url/citation/http anywhere). oracle_guard marks a fact "supported" ONLY if an oracle CITATION entails it → with no citations, EVERY non-"gsa" fact → "unsupported", every "gsa"/officer/club fact → "we_are_authority" (the _INTERNAL_HINTS heuristic). RESULT: all 49 vital control facts = DROPPED_ORACLE. SC3 passes (blind flagged) but SC2 is vacuous (0 supported positive-control facts) and κ is uncomputable (no non-dropped facts). The instrument as designed CANNOT measure debt with this oracle.
  SECONDARY FINDING: Brave has no NJIT context. "who are the GSA officers"→answered about US General Services Admin + Geological Society of America. "chair of computer science"→generic multi-university list. "Shantanu Sharma research"→CONFLATED several homonymous people. BUT NJIT-explicit phrasing FIXES it: "chair of the CS dept AT NJIT"→correct Vincent Oria. So oracle queries must be NJIT-scoped.
  ✅ BLOCKER RESOLVED (2026-07-06, read the Brave manual per owner). Brave AI Grounding DOES return citations — our oracle_brave.py just called it wrong. FIX: request must send stream:true + enable_citations:true (top-level for raw HTTP; SDK uses extra_body). Advanced params (citations/entities/research) REQUIRE stream:true. Response is SSE: accumulate choices[0].delta.content; citations arrive INLINE as <citation>{"url","snippet","number","start_index","end_index",...}</citation> tags. LIVE-TESTED: "chair of CS dept at NJIT" → correct Vincent Oria answer + 3 real NJIT citations (cs.njit.edu/welcome-chairperson etc.) each WITH a snippet (the supporting excerpt). So the original design premise (grounded oracle that cites) HOLDS — my "no citations" was a request-format bug (stream:false + no enable_citations), NOT a product limitation.
  ⏭ PLAN = Option D (fix the request, keep the guard as designed): (1) rewrite oracle_brave.ask_oracle to POST stream:true+enable_citations:true, parse the SSE stream, strip <citation> tags from answer text, return citations [url + snippet]; add snippet to OracleCitation. (2) guard can entail against the citation SNIPPET directly (faster/more reliable than fetching each url; fall back to fetch). (3) NJIT-scope oracle queries for Set A/B (secondary finding still stands — "at NJIT" fixes entity ambiguity; Set C stays unscoped). Then re-run 8 controls → confirm supported facts + κ flow → full Set A. Needs quick reviewer nod (expert-review gate) before re-run. Note: streaming may not need the ~$ change; still ~$0.057/query.
  Endpoints (verified): ANSWERS chat/completions (BRAVE_ANSWERS_API_KEY) = answer+citations WHEN stream+enable_citations set. Web SEARCH /web/search (BRAVE_API_KEY) = plain source URLs (what live-fallback uses). Two different products.
  T14 also built: gate.py (R10 real sys.exit(2) halt on SC2 pos-control-miss>1 / SC3 blind-not-flagged; M6 oracle-reachable precondition; all injectable/tested w/o spend), pathlabel.py (label_path for Set-B), and CURATED CONTROL FILES: out/controls_positive.txt (5: CS chair, GSA officers, Shantanu Sharma ×2, YWCC dean — all locally verified we answer completely) + out/controls_internal.txt (3 oracle-blind GSA-internal). Owner may eyeball/adjust controls before the run.
  ⏭ NEXT = T14 (live integration gate) + T15 (full run) — these are the SPEND-GATED tasks. STOP HERE for owner: T14/T15 need (a) owner "go", (b) Brave ANSWERS key + cap raised, (c) ~3 adjudication sessions. Per owner's plan: run SET A FIRST (50 real-log Qs), κ≥0.6 (SC1) is the go/no-go — owner reviews the numbers before spending on Sets B/C.
  T13 report.py = debt headline + SC1-SC6 gates + cluster-robust bootstrap CI + required-N power analysis + per-set compare. Amendment .superpowers/sdd/task-13-amendment.md. Fixed 2 Minor render edges post-review (qfm floor honors docstring; Agresti-Coull per-stage N avoids p=1 "~0 questions" collapse).
  T14 will create: pathlabel.py (label_path: q→router_hit/rag/live_fallback/abstain), out/controls_positive.txt (5), out/controls_internal.txt (3 oracle-blind). R10 real sys.exit halt on SC2/SC3 fail + M6 Brave-200 precondition. R14 B2: adjudicate the 8 control Qs' facts FIRST (cheap early κ read) before any full-set spend; B5: verify route()/V2Retriever/MATCH all run under the mode=ro handle w/o incidental writes.
  T12 = 3-set samplers (sample.py: sample_set_a/b/c, each 50) + run_pilot.py (per-set facts_{set}.jsonl, R3 LIVE_ENABLED=0 answer capture). Amendment .superpowers/sdd/task-12-amendment.md.
  ⚠️ REAL-DATA DEVIATION owner should know: Set A can't use R0's `was_answered` split — that column is uniformly 0 (DEAD) in the live `questions` table. Stratified Set A by CONFIDENCE bands instead (hi≥70:471, lo:261, deflect/zero:45 over 777 distinct usable Qs). Serves R0's intent (spans answer-quality). Reviewer confirmed sound.
  T12 caught+fixed a Critical (3807423): _extract_answer leaked ANSI/header-parenthetical/source_note noise into our_answer vs the REAL ask.sh output — now strips ANSI, drops header remainder+rule lines, stops at [source_note=], de-indents. Realistic regression test added.
  T11 adjudicate.py = key-based κ (join on fact_id=sha1(question␟fact_text), NOT row position) + nugget-set precision/recall (Guardrail A) + B1 emit DROPPED_ORACLE rows w/ human_guard_ok rescue col. Amendment in .superpowers/sdd/task-11-amendment.md. Review clean, 10 tests.
  T10 classify.py = per-fact orchestrator (guard→IN_ANSWER→NOT_OWNED→OWNED_NOT_SURFACED, deps-injected); R4 three-way lean already lives in entailment.entails (Task 3) so NO amendment needed — verbatim plan transcription, review clean.
  T9 review clean; minor inherited nit: attribute._excluded_types lacks lowercase/per-org override vs prod _load_exclude → logged for final whole-branch review, not a defect.
  REMAINING code tasks before the SPEND gate: T10 classify · T11 adjudicate(R8 key-based+nugget-set+B1) · T12 sample+run_pilot(R0 3×50; R3 LIVE_ENABLED=0) · T13 report(R9 SC6/per-set; R13 power; R14). Then T14 live gate + T15 full run (Set A FIRST — κ≥0.6 SC1 is the go/no-go; owner reviews numbers before Sets B/C spend).

## Plan shape (15 tasks, package `eval/processing_debt/`)
T1 scaffold+types+ro-conn · T2 oracle_brave(cached Brave client) · T3 entailment(Granite judge) ·
T4 nuggetize(atomic+vital/okay) · T5 fixture DB · T6 **presence_check (CRUX, kg/fts/embed/grep union)** ·
T7 xray(live route+retriever) · T8 oracle_guard(citation NLI + authority) · T9 erag+attribute(stage tree) ·
T10 classify(orchestrate 3-way) · T11 adjudicate(CSV+Cohen's κ) · T12 sample+run_pilot(driver) ·
T13 report(debt%+SC gates) · T14 LIVE integration gate (controls FIRST = SC2/SC3 go/no-go before spend) ·
T15 full 50 run + 100% adjudication + report. Each task = failing test→impl→pass→commit. Read-only, ≤$2.85.
KEY: T14 runs the 5 positive + 3 oracle-blind controls BEFORE spending on the 50 — if positive-control
owned-misses >1, STOP (instrument broken). SC1 κ≥0.6 gates any future scale-up.

## Design in one screen (full spec = the design .md)
Pilot N=50 Qs from docs/SampleQuestions/. Per Q: Brave oracle answer+citations → nuggetize (vital/okay
materiality) → oracle-guard (drop facts the oracle's own citation doesn't support; WE_ARE_AUTHORITY flag
on GSA-internal) → per surviving VITAL fact classify 3 ways: IN_ANSWER / OWNED_NOT_SURFACED (=processing
debt, gold) / NOT_OWNED (knowledge gap). **CRUX = exhaustive non-production presence_check.py** (4 probes
UNION: kg SQL + FTS over ALL types incl. excluded `publication` + brute-force embed k=100 + grep) — avoids
the circular trap. Owned-misses → stage-attributed (ROUTER/POOL/RANK/COMPOSE/CONFIG/UNRESOLVED) via
ask.sh X-ray + eRAG per-chunk utility. REUSE RAGChecker(decompose+entail, Ollama-swappable) + eRAG +
AutoNuggetizer protocol + ARES(scaffold only). Pilot deliverable = VALIDATE THE INSTRUMENT (100% human
adjudication, report Cohen's κ), not the debt number. Pre-registered SC1–SC6 (κ≥0.6 gate to scale;
positive+oracle-blind controls; ≥5 owned-misses; ≥70% unambiguous attribution). Files in
`eval/processing_debt/` (13 small units). Scale-up + distillation "fix" phase = DEFERRED (ND1/ND2).

## Research verdict (the big one)
A **ready-made, code-backed stack exists** — we assemble, not invent:
- **RAGChecker** (arXiv:2408.08067, [code](https://github.com/amazon-science/RAGChecker)) = PRIMARY tool.
  Its *Claim Recall* with `gt_answer`=Brave oracle answer + a **local Ollama** judge (extractor/checker
  swappable) IS our "facts-we-own-but-didn't-surface" metric, near out-of-box. Retriever-vs-generator
  metric split gives stage attribution.
- **eRAG** (arXiv:2404.13781, [code](https://github.com/alirezasalemi7/eRAG)) = per-doc counterfactual
  utility → retrieval-stage attribution (which chunk carried the fact).
- **AutoNuggetizer** (TREC RAG, arXiv:2411.09607) = vital/okay materiality → kills verbosity bias.
- **ARES** (arXiv:2311.09476) PPI = calibrate local judge on ~50–300 human labels; report **Cohen's κ**
  (kappa-deflation: raw 0.78–0.85 → κ 0.38–0.51).
- Oracle-guard grounded: generative search engines only ~51.5% citation-supported (Liu 2304.09848).

## ⚠️ Research nuance that MODIFIES Fable fix #1
RAGChecker's Claim Recall uses `retrieved_context` = what OUR PRODUCTION pipeline retrieved → that's the
circular trap. So we still must BUILD a separate **exhaustive corpus-presence check** (SQL + FTS over ALL
types incl. `publication` + brute-force embedding + grep) as a THIRD outcome. Claims split 3 ways:
in-answer / in-corpus-but-not-surfaced / not-in-corpus. RAGChecker gives the first two; the exhaustive
check is ours to build. THIS is the core custom piece of the design.

## Under-evidenced (needs follow-up research IF we reach those phases)
- PairDistill / distilling mined router-rerank preferences into retriever = the eventual "fix" phase —
  NO surviving verified claims in this pass. Defer + re-research later.
- Judge reliability on ENTITY-HEAVY SHORT answers (our exact case) — unmeasured in lit; the pilot's
  human-adjudication step is how we get this number for our own domain.

## What this project is
Diagnostic-oracle gap-finder. Brave Answers (answer + citations) as a **diagnostic**, not a knowledge
source, to measure **Processing Debt** = % of facts we already own that our pipeline fails to surface,
attributed per stage (router / pool / rerank / compose). Fix the processing gaps → better answers with
zero new crawling. See `README.md` for thesis + mechanism + cost.

## Decisions locked
- ✅ Oracle = **Brave Answers** API (separate subscription: `BRAVE_ANSWERS_API_KEY`, endpoint
  `POST https://api.search.brave.com/res/v1/chat/completions`, OpenAI-compatible, model `brave-pro`).
  VERIFIED WORKING 2026-07-06. Separate quota from prod live-fallback (Brave *Search* key).
- ✅ Cost known: ~$0.057/query; $5 free credits/mo ≈ ~88 queries; budget ~$30; both Brave plans set to
  "Free credits only" (no card-billing risk — halts at credit exhaustion).
- ✅ **Pilot-first** (~50 Qs, ~$3–4) before any scale decision. Full 2000 (~$114) rejected.
- ✅ Research report + all md live in THIS folder; global memory untouched.

## Fable's 4 REQUIRED fixes (fold into design — see fable review file)
1. **Presence check must be non-production / exhaustive** (SQL + FTS over ALL types incl. excluded
   `publication` + brute-force embedding + grep). NOT "re-run production retrieval" — that's circular
   and understates debt.
2. **Materiality filter** — only score facts responsive to the question (TREC vital/okay), else
   verbosity asymmetry inflates our miss rate.
3. **Human-adjudicate 100% of pilot** + report judge–human kappa. Pilot's real deliverable = validate
   the instrument (kappa ≥ 0.8), not the debt number.
4. **Read RAGChecker (arXiv:2408.08067) before finalizing metrics** — may be half the build already.

## Other constraints / notes from review
- Oracle citation-support only ~51.5% (Liu et al. 2304.09848) → oracle-guard drops a big minority;
  halves yield/dollar. Budget for it.
- Oracle blind to GSA-internal (Wix/dashboard) data → complement with corpus-driven auto-eval harness.
- Metric is DEMAND-weighted (questions people ask), distinct from corpus-recall (sampler). Report both.
- 1000 web-needing Qs can't yield a processing-debt number (facts not owned) → separate track; also
  burns our Brave *Search* free quota. Pilot ~40 DB / ~15 web.
- Stratify pilot by PIPELINE PATH (router-hit / RAG / live-fallback / abstain). Add positive controls
  (we answer well → must show 0 miss) + GSA-internal controls (oracle must fail).

## Open questions (for owner, post-research)
- Primary deliverable: one-time gap report vs reusable harness vs both? (asked, not yet answered)
- Scale ceiling / budget confirm after pilot proves the instrument.
- Whether to also wire the mined router/rerank signal into a PairDistill-style fix (later phase).

## Next action
1. ⏳ Wait for deep-research workflow → write report into this folder → synthesize for owner.
2. Owner decides: proceed to design (with Fable's 4 fixes) or adjust.
3. Then: brainstorming → design spec (in this folder) → expert review → build TDD (per repo hard gate).

## Log
- 2026-07-06: Brave Answers endpoint discovered + verified. Cost modeled. Fable review obtained
  (verdict: proceed after 4 fixes; lit pass in parallel). Deep-research launched. This workspace created.
- 2026-07-06: Deep-research DONE (107 agents, 24 sources, 23/25 claims verified). Report saved. Verdict:
  RAGChecker+eRAG+AutoNuggetizer+ARES stack is reusable on our Ollama stack; exhaustive corpus-presence
  check is the one custom piece.
- 2026-07-06: Gave Fable the full package. Fable: no deeper pass needed → wrote full design spec
  (`2026-07-06-design-processing-debt-pilot.md`), saved verbatim. AWAITING OWNER SIGN-OFF.
