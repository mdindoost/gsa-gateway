# Accuracy: Observability Map + 👎 Feedback Review + Fix Backlog (2026-06-22)

**Purpose:** the single place to track accuracy problems and their fixes. Born from two owner questions:
(1) where are questions kept + is there a "something's wrong" flag? (2) what improves whole-system accuracy?

---

## 1. Observability map — where the signal lives
- **`questions` table (879 rows):** every Q with `question_text`, `matched_topic`, **`confidence`**, `was_answered`,
  `platform`, `mode`, `org_id`, timestamp (user IDs hashed).
- **`response_feedback` table:** 👍/👎/🔄 + a **`detail` reason tag** (`off_topic`/`incomplete`/`wrong_info`).
  Current: **46 👎, 13 👍, 22 🔄.**
- **`feedback` table:** free-text student feedback. Plus bot logs.
- **Dashboard → Analytics tab:** "Answer Rate" (confidence≥50), 👍/👎 counts, "Questions to Add to KB" (confidence<50).

## 2. ⚠️ The trap: confidence ≠ correctness
Dashboard headline = **"Answer Rate 91.5%"** (804/879 at confidence≥50). **This is a vanity metric.** The 46 👎'd
answers scored **confidence 54–100** — i.e. the real failures live *inside* the "answered well" bucket. So:
- **`confidence`/answer-rate measures RETRIEVAL confidence, not answer correctness.** Do NOT trust it as quality.
- **The only trustworthy "something is wrong" signal is 👎 + free-text feedback** — and the 👎 already carry a reason
  tag. There is **NO active alert** today (monitoring is dashboard-pull); the 46 👎 sat unreviewed until 2026-06-22.

## 3. The 46 👎 — reviewed, by root cause (the actual failure modes)
(~23 distinct questions; each 👎 has a paired blank + reason-tagged row.)

| Bucket | Examples | Reason tags | Root cause | Fix |
|---|---|---|---|---|
| **A. Conversation context loss** (follow-ups/ellipsis) | "What is his position", "What about for BME?", "Why you didnt list him", "What about the official one?", "What is his gield of research", "Cs department", "Wrong" | off_topic, wrong_info | Each message answered STANDALONE → matched the wrong prior topic | **Carry prior-turn context** (backlog #2). Highest-evidence bug. |
| **B. Research-area people enumeration** | "people working on graph research"→1 name, "phd students work on graph"→VP, "professors in graph"→Ding, "someone in cs working on X"→Borcea | incomplete, off_topic | Should be KG list-by-area; "graph" unresolved as an area → RAG grabs one name | Recheck under v2.1 router; **add "graph"/missing area tags** to the taxonomy; ensure list-by-area routes |
| **C. Content gaps** | "extend PhD to 6 yrs"→Medical Leave, "PhD timeline from application"→PhD Club, "lavender graduation", "December graduates walk", "management college", "Koutis awards", "event today" | incomplete, off_topic, wrong_info | No authoritative KB data → RAG grabs a wrong-but-plausible chunk | **Prose-harvest** (registrar/grad-studies/commencement) + person awards data + events freshness |
| **D. Multi-part / incomplete** | "travel award" (incomplete), "speakers of MMI AND officers of GSA", "register for MMI 2026"→wrong edition | incomplete, wrong_info | Compose returns partial / can't satisfy a two-part ask / wrong instance | Retrieval+compose quality; multi-intent handling |
| **E. Identity-intent miss** | "Hi, introduce yourself thoroughly" → matched Career Development/Resumes | wrong_info | Identity intent not detected → fell to RAG | Intent detector: catch "introduce yourself"/identity phrasings |

## 4. Fix backlog — prioritized (ROI-ordered)
| # | Action | Why / evidence | Effort | Status |
|---|---|---|---|---|
| 1 | **Close the feedback loop**: review 👎 (+ `detail`) regularly, categorize, fix root cause | The signal exists + is reason-tagged; was unreviewed. Zero new infra. | low | **STARTED — this doc** |
| 2 | **Conversation follow-up context** (carry prior turn so "what about BME?" resolves) | Bucket A — biggest 👎 cluster; known deferred #2 | med | open |
| 3 | **Active failure digest** (weekly "what got 👎 / deflected", not dashboard-pull) | The "flag that tells us something's wrong" the owner asked for; today passive | low–med | open |
| 4 | **Use `eval.sh` as the accuracy gate** + feed the 👎/low-conf Qs into `eval/questions.txt` | eval auto-judges CORRECTNESS via the real pipeline (unlike confidence); suite should track real traffic [[feedback_grow_correctness_suite]] | low | open |
| 5 | **Content ingestion** (prose-harvest, spec `d35fa69`) | Bucket C — immigration/registrar/parking/commencement gaps | (designed) | handed off |
| 6 | **Area-taxonomy gaps** (e.g. "graph") + verify list-by-area routing under v2.1 | Bucket B | low–med | open |
| 7 | **Identity-intent phrasings** ("introduce yourself") | Bucket E | low | open |
| 8 | **(Deeper) abstention calibration** — confident-wrong → honest "I'm not sure, check X" | Structural fix for the confidence≠correctness trap; router abstention built-but-OFF | med | deferred (Phase-2) |

## 5. One-line takeaway
The 91.5% answer-rate is a vanity number; **the real accuracy signal is the reason-tagged 👎, and it points at
conversation-context loss (A) as the #1 bug**, with content gaps (C) the largest volume. Cheapest win: close the
feedback loop (#1) + an active digest (#3). Most impactful build: follow-up context (#2). Right metric: `eval.sh` (#4),
not confidence.

*Related: prose-harvest design `docs/superpowers/specs/2026-06-22-njit-prose-harvest-design.md`; [[project_day_to_day_intents]]; [[feedback_grow_correctness_suite]].*
