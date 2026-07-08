# Expert Reviews — Judge-Fix Delta-Design (2026-07-07)

Two independent reviews of `2026-07-07-judge-fix-delta-design.md`. Both = conditional-go.

---
## Review A — RAG / LLM-evaluation researcher   →  VERDICT: SOUND AFTER CHANGES

Core swap (granite+unsure-lean → NLI + confident-present) is methodologically legitimate and kills the
proven bug; NLI-as-checker is lit-standard (RAGChecker/RefChecker/ALCE) and the entailment DIRECTION is
correct (premise=span, hypothesis=fact). But as designed it fixes the over-report while introducing an
under-report bias + inconsistent boundary, and leaves one artifact that corrupts κ. Six concerns, ranked:

1. **(κ-critical) Pull pronoun-resolution into THIS pass.** 16/121 owned-misses open with a dangling
   pronoun ("He…","The program…") — unjudgeable in isolation. Human resolves the subject from the question;
   NLI sees no subject → folds to NOT-present → systematic machine–human disagreement on ~13% of facts →
   structurally depresses κ (the go/no-go stat). Min fix: prepend the question's entity, OR exclude
   dangling-pronoun nuggets from the κ denominator + report as a separate "unjudgeable" bucket. (This is
   PROJECT_MEMORY Guardrail A, silently dropped by the delta-design.)
2. **(magnitude+signal) NLI is off-distribution on the structured/long spans where the REAL debt lives.**
   kg_probe span = `name|type|attrs|edges` blob; fts_probe = FULL content (512-truncated). DeBERTa-NLI
   degrades on long premises + reads terse structured blobs as neutral → false NOT_OWNED → under-reports
   the KG debt that is the thesis's strongest evidence. The 11-pair test is prose-only → blind to this.
   The [0.35,0.5) band does NOT catch it (high-neutral lands BELOW 0.35, vanishes). Fix: before spending,
   run a ~20-30 pair HARD slice built from CONFIRMED genuine owned-misses already in facts_A.jsonl
   (KG-span + long-page) and confirm NLI scores them ≥ HI. Zero Brave spend. Gate the full re-run on it.
3. **HI=0.5/LO=0.35 unvalidated** — 11 pairs all scored 0.00 or ≥0.86 → validates the MARGIN not the CUT.
   Real Set A will populate the boundary; threshold sets debt there, biased toward under-report. Calibrate
   on held-out gold; do NOT tune on the same labels you report κ on (circularity → breaks pre-registration).
4. **Cut escalation from the pilot.** Routing only [0.35,0.5) to gemma = non-monotonic boundary; gemma has
   zero evidence of being better (same 7/7+4/4 on the EASY set). In a 100%-adjudicated pilot no tiebreaker
   is needed — record P(entail), let the human decide. Escalation is a scale concern (deferred).
5. **Off-topic questions**: deferring the topicality gate is OK ONLY IF debt is reported as a with/without
   sensitivity band (74%→~66%), not a single number.
6. **(integrity) Loudly flag the §3.2 conservatism REVERSAL.** Original design chose generous-presence
   (under-report debt-as-absent); this flips to strict. Correct response to a 2× over-report, but per the
   repo's "review against plan" hard line, state it explicitly + present two numbers (confident headline +
   low-conf band) as the reconciliation.

Not-concerns: neutral→NOT-present fold is correct for a presence check; the re-run DOES validate the new
judge (adjudicate.py is key-based ground-truth presence, not agreement-with-machine — safe to reuse labels).

---
## Review B — Senior software engineer   →  VERDICT: BUILD AFTER FIXES

Direction sound + evidence-backed. classify.py interface CAN stay stable (confirmed). Reranker pattern
genuinely reusable (nli_bakeoff.py already proved it: onnxruntime+tokenizers, conditional token_type_ids).

**MUST-FIX (blocking):**
1. **The low-conf band is never actually surfaced — it silently falls into NOT_OWNED.** Design claims a
   reported bucket but nothing implements it: presence_check.py:132 `unsure_only` evaluates False when the
   new lean sets present=False; classify.py:33-36 routes any not-present → NOT_OWNED (no low-conf branch);
   report.py has NO fact-level low-conf bucket and no code derives `unsure_rates`. → violates "never
   silently drop facts" + removes the facts the human must adjudicate for honest κ. Fix: populate a
   low_conf field (types.py:42 has room) on NON-present results + add a report-side bucket.
2. **Escalation contradicts the low-conf band AND its cost is unbounded.** Design says both "re-judge every
   borderline pair with gemma, take its verdict" AND "keep borderline as low-conf, not present" — two fates
   for the same band, unspecified which wins. Cost: "rare/cheap" rests on the entity-MISMATCH gold set;
   real genuine-debt cases are SAME-SUBJECT-PARTIAL = exactly what lands mid-band → hundreds–thousands of
   700–1300ms gemma calls → reintroduces the ~4hr runtime batching was meant to kill; also runs per-pair,
   defeating the batch. Fix: resolve band-vs-escalate semantics + bound it (or drop for pilot).

**SHOULD-FIX (fold in):**
3. **512-truncation fights M3.** fts/embed_probe return FULL document spans; NLI truncates at 512 → if the
   entailing sentence is past token 512 → false NOT_OWNED (forbidden under-report). Higher risk than the
   reranker (whole docs vs ~350-tok chunks). Fix: window/sentence-split long spans to the match neighborhood
   before NLI (grep_probe already windows via `_window`; fts/embed do not).
4. **Premise/hypothesis direction is load-bearing + unstated.** Must encode (premise=span, hypothesis=fact);
   nli_bakeoff.py:47 is correct `(span,fact)` but the design writes `score(fact, span)` fact-first. If the
   implementer encodes (fact,span) the direction inverts silently. Make encoding order explicit.
5. **The prompt fix was dropped.** Findings named model+PROMPT+lean. NLI has no prompt, but gemma-escalation
   and any PD_JUDGE=llama|gemma|granite fallback still use the weak `_SYSTEM` ("'unsure' if related") — the
   exact hedge that caused the bug. Adopt IMPROVED_SYSTEM (judge_bakeoff.py:52-58) for all generative backends.

**MINOR (TDD):** (6) update test_presence_unsure_leans_present (lean inverts) + add low-conf test + batch
seam; (7) rebuild the run harness feeding build_report (run_setA.py absent, nothing derives unsure_rates);
(8) sub-batch NLI (~32) + read PD_JUDGE/PD_ESCALATE once at load.

**On non-goals:** deferring self-contained nuggets + topicality is CORRECTLY scoped out for the κ re-run —
κ measures agreement on whatever nuggets exist, both parties see the same nuggets, so κ stays valid; they
inflate denominator/noise not agreement. Only Must-Fix 1 threatens κ.

---
## The one DISAGREEMENT for the judge to settle
**Dangling-pronoun nuggets (~13% of facts):**
- **RAG (Review A #1):** κ-CRITICAL — pull pronoun-resolution/exclusion into THIS pass, else κ is
  structurally depressed by machine–human disagreement on unjudgeable nuggets.
- **Senior (Review B, non-goals):** deferring nuggets is FINE for κ — both human and machine see the SAME
  (imperfect) nuggets, so the agreement statistic stays valid; nuggets inflate noise, not disagreement.

Both agree Must-Fix 1 (surface the low-conf band) is the real κ threat.
