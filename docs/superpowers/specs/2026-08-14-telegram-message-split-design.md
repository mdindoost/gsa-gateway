# Long-answer delivery: Telegram message splitting + error handler

**Date:** 2026-08-14
**Status:** DESIGN v2 — REVISED after senior-eng review + Fable sign-off. Awaiting owner diff sign-off.
**Reviews:** Fable → APPROVED WITH CHANGES. Senior-eng → APPROVE-WITH-CHANGES (2 blockers, both folded in below).
**Author:** Claude (Opus 5)
**Trigger:** "who is Taro Narahara" on Telegram returns **nothing at all**.

---

## 1. The bug (evidence, not inference)

`logs`/`telegram_bot.log` at 2026-08-14 16:59:35 and 17:02:13 (both matching the owner's
attempts):

```
POST .../sendMessage "HTTP/1.1 400 Bad Request"
telegram.error.BadRequest: Message is too long
  bot/connectors/telegram_connector.py:258  reply_text(html_text, parse_mode="HTML", ...)
  ... during handling of the above, another exception:
  bot/connectors/telegram_connector.py:262  reply_text(resp.text, reply_markup=keyboard)
[ERROR] telegram.ext.Application: No error handlers are registered, logging exception.
```

Measured: the `entity_card` answer for Taro Narahara is **~7,100 characters**
(`bash scripts/ask.sh "who is Taro Narahara" --answer | awk '/5. FINAL LLM ANSWER/{f=1} f' | wc -c`
→ 7253 incl. the section header). Telegram's per-message text limit is **4,096**.

**This is a delivery bug, not a retrieval or data bug.** The pipeline produces a correct,
complete answer (verified end-to-end: router → entity_card → deterministic suffix with Scholar
links). It is thrown away at the transport.

### Two defects, compounding

| # | Defect | Effect |
|---|--------|--------|
| **D1** | No length splitting at any Telegram send site | Any answer > 4,096 chars 400s |
| **D2** | The `except` fallback at :262 re-sends **the same oversized text** as plain text | The fallback 400s too — so there is no degraded path, just silence |
| **D3** | No `Application.add_error_handler` registered | The failure is invisible to the user; only the log knows |

D2 is what turns "bad formatting" into "complete silence." The fallback was written for a
*parse-mode* failure (its original purpose) and is wrong for a *length* failure.

### Blast radius — not Narahara-specific

Any person whose NJIT profile is long. The `entity_card` concatenates about + full research
statement (with raw `<b>`/`<br>` from the source) + education + every course taught + the full
service list + Scholar links. Narahara is simply the first one the owner happened to ask about.
Same failure will hit any long `knowledge_items` prose answer or a verbose live-fallback extract.

### Per-platform status

| Platform | Limit | Current behavior | Verdict |
|----------|-------|------------------|---------|
| **Telegram** | 4,096 chars (message text) | 400 → silence | 🔴 **BROKEN** |
| **Discord** | 4,096 (embed description) | `body[:4093] + "..."` (`chat.py:130`) | 🟡 **Delivered but silently truncated** |
| **GroupMe** | 1,000 | already chunks (`groupme_connector.py:53 _chunk`) | 🟢 OK |

GroupMe already solved this. The pattern to follow exists in-repo.

---

## 2. Goals

1. **G1** — A long answer is *delivered in full* on Telegram, never silently dropped. (HARD LINE:
   NJIT content is served verbatim, never withheld — `feedback_verbatim_strong_reason_exception`.)
2. **G2** — Chunks are each **tag-balanced** for `parse_mode="HTML"` (no split tag, no split entity,
   no unbalanced tag), with HTML *validity* backstopped by the mandatory per-chunk plain-text
   fallback (G4).
   > **Revised after review.** v1 claimed splitting *guarantees* validity. False: `_tg_html` applies
   > `**bold**` before `*italic*` (`telegram_connector.py:44-45`, both `re.S`), so crossed emphasis
   > renders `<b>bold <i>ital</b> rest</i>` — tag-balanced, equal counts, and **rejected by Telegram**
   > with `Can't parse entities`. Splitting changes which regex matches where, so a split can create
   > this from input that was fine whole. That is a pre-existing `_tg_html` defect, not a splitting
   > defect; the honest property splitting buys is tag-balance, and G4 is what makes it safe.
3. **G3** — The feedback keyboard (👍/👎/🔄) and the footer appear **once**, on the last chunk —
   the buttons must keep working (`feedback_structured_no_buttons`: ALL answers get buttons).
4. **G4** — The plain-text fallback is itself length-safe, so a parse failure degrades to readable
   text instead of a second 400.
5. **G5** — A send failure is never silent again: a global error handler logs it and tells the user
   something went wrong.
6. **G6** — Applies to **all four** answer-bearing Telegram send sites, not just the one that
   happened to fail.
7. **G7 (Part B, separable)** — Discord stops silently truncating at 4,093.

**Non-goals:** shortening the entity card, trimming the research statement, or gating long content.
That would violate the verbatim hard line — the fix is transport-level.

---

## 3. Design

### 3.1 New module `bot/core/msg_split.py`

> **⚠️ v1's algorithm was BROKEN and is replaced.** The senior-eng reviewer implemented v1 §3.1
> faithfully and ran it: `"&amp;" * 4000` at limit 3900 produced **2 chunks, max rendered length
> 19,500 UTF-16 units — 4.7× over the 4,096 cap.** Cause: v1 borrowed GroupMe's boundary ladder,
> which takes its window in **plain** space (`window = remaining[:limit]`), while v1's loop
> condition was in **rendered** space. The units are not interchangeable, because `html.escape`
> expands `&` → `&amp;` (1 → 5 units). The loop terminates, so no hang — it just silently emits
> oversized chunks that 400 exactly as today, **while every test in v1 §4 passes.**

The fix also makes the design *smaller*. Key insight from review: the Bot API specifies
`sendMessage.text` as *"1-4096 characters **after entities parsing**"* — Telegram strips the HTML
tags and decodes entities *before* counting. Rendering only ever shortens the parsed result
(`&amp;`→`&`, `**b**`→`b`, `[label](url)`→`label`; nothing is ever added). Therefore
**`utf16_len(plain) <= 4096` is already a sufficient bound**, and a tighter one. That deletes
`split_rendered`, the render-in-the-loop, and the O(n²) cost in one move.

```python
def utf16_len(s: str) -> int:
    """Telegram counts in UTF-16 code units, not Python characters. An emoji
    (🎓 🔗 💡 — all in our footers/suffixes) is a surrogate pair = 2 units, not 1."""
    return len(s.encode("utf-16-le")) // 2


def split_plain(text, limit, *, atomic_spans=MASKED_LINK_RE) -> list[str]:
    """Split PLAIN markdown into chunks of <= `limit` UTF-16 units.
    Boundary ladder: paragraph (\\n\\n) -> line (\\n) -> space -> hard cut.
    Never cuts inside an `atomic_spans` match. Guarantees >= 1 char of progress
    per iteration (so it provably terminates) and never emits an empty chunk."""


def split_for_telegram(text, limit, render) -> list[str]:
    """split_plain + a POST-CONDITION safety net: if any chunk's RENDERED form still
    exceeds `limit` (only reachable if Telegram counts pre-parse, contra the documented
    behavior), re-split that chunk at a halved budget until it fits or hits the floor.
    Ends with: assert all(utf16_len(render(c)) <= limit or len(c) <= FLOOR for c in chunks)"""
```

**Why keep `split_for_telegram`'s net if plain-budgeting is provably sufficient?** Because the
"after entities parsing" wording is the *documented* behavior and field reports are not unanimous.
Budgeting on plain keeps the common path linear and simple; the post-condition makes the module
correct under *either* reading of the limit, and it is the explicit assertion the reviewer required.
On real answers (≤1 link-dense chunk) the net never fires.

**Required properties, all pinned by tests:**
- **≥1-char floor** — progress is always available (worst single char `&` → 5 units ≪ budget), so
  termination is provable rather than assumed. v1 never stated this.
- **No empty chunks** — filter `if c.strip()` before sending; `reply_text("")` is a Telegram 400
  (`message text is empty`), i.e. a *second* silent-failure mode.
- **Masked-link atomicity** (Fable) — a cut inside `[label](url)` exposes a raw half-URL, uglier
  than a stray `**`. Scholar links from `deterministic_suffix` sit at the *end* of long cards,
  exactly where cuts land. Non-negotiable.
- **Content invariant** — stated as *"no non-whitespace character is lost"*
  (`"".join(chunks)` ≡ original, whitespace-normalized). v1's test 2 said "concatenation preserves
  all content", which the `.strip()`-ing ladder it pointed at would have failed.

### 3.2 The key decision: split the **plain** text, render each chunk (not: split the HTML)

Two candidate approaches:

- **(A) — CHOSEN.** Split `resp.text` (our Discord-style markdown) on paragraph boundaries, then
  apply `_tg_html()` to *each chunk independently*. Every chunk is therefore tag-balanced **by
  construction** — there is no way to emit a split `<a href=` or a split `&amp;`, because each
  chunk is rendered from scratch. Budgeting accounts for HTML expansion by measuring
  `utf16_len(_tg_html(candidate))` while growing a chunk, not the plain length.
- (B) — Rejected. Scan the rendered HTML, track open tags, cut only outside tags/entities, and
  re-open the balance at the next chunk. Correct in principle, but it is a hand-rolled HTML parser
  in the serving path — far more bug surface for zero user-visible gain.

**Accepted trade-off of (A):** a markdown span that straddles a chunk boundary (`**bold …` in
chunk 1, `… bold**` in chunk 2) renders as literal asterisks. Paragraph-first boundary preference
makes this rare, and our answers put bold inside a line, never across a blank line. Cosmetic
worst case, never a 400. **Masked links are exempted** (atomic spans, §3.1) because their failure
mode — a raw half-URL — is materially worse than a stray asterisk.

**Reviewer's check on raw source HTML (non-issue, recorded):** `resp.text` can contain raw `<b>`/
`<br>` from NJIT prose. `_tg_html` html-escapes *before* constructing any tag, so those become inert
`&lt;b&gt;`, and escaping is per-chunk idempotent. Corpus-wide counts: `<b>`:3, `<br`:7, `<i>`:7,
`<a href`:2 — real but rare, and harmless under (A).

**One-line hardening while we're here (finding #2c):** `_tg_html` line 40 uses
`html.escape(text, quote=False)`, so a `"` survives into the `href` built at line 43 —
`[x](https://a.com/?q=")` yields a broken/injectable attribute. Escape the URL with `quote=True`
at the point of `<a href=…>` construction. Pre-existing, one character, in scope.

### 3.3 One send path: `_reply_chunked`

Replace all four ad-hoc `reply_text` + bare-`except` blocks with a single private method:

```python
from telegram.constants import MessageLimit

TG_LIMIT = MessageLimit.MAX_TEXT_LENGTH   # 4096, verified on the installed PTB 22.7 — not hardcoded
TG_BUDGET = TG_LIMIT - 196                # 196 = measured footer (145 units) + 51 slack for the
                                          # "\n\n" join and a trailing partial word. Derived, not magic.

async def _reply_chunked(self, message, text, *, footer_html="", keyboard=None) -> None:
    """Send `text` as one or more messages, each tag-balanced and within Telegram's limit.
    Footer + keyboard ride on the LAST chunk only. Per-chunk plain-text fallback on a
    parse failure — the fallback is itself split, so it can never 400 on length."""
```

The `3900` magic constant from v1 is gone (Fable finding 3 + the project's *derive-limits-from-real-
capacity* rule). The footer was **measured**, not guessed: `🔗 <a href=…>Source</a>` + brand line =
**145 UTF-16 units**.

Behavior:
- Budget the **last** chunk against `TG_BUDGET - utf16_len(footer_html)` so footer + text fit.
- Append `footer_html` to the last chunk only; attach `keyboard` to the last message only.
- Send chunks in order, sequentially (`await` each — preserves ordering).
- Per chunk: `try` HTML → `except BadRequest` → send that chunk's **plain** text, re-split at the
  plain budget. Fixes D2.
- **`RetryAfter` retry (finding #6):** catch `telegram.error.RetryAfter`,
  `await asyncio.sleep(exc.retry_after + 0.5)`, retry that chunk once. Without this, chunk 1 lands,
  chunk 2 is dropped, and the user gets a truncated answer with no buttons and no error — *exactly
  the silent drop G1 exists to prevent*. v1 §5 waved this away with "far under Telegram's ~30/sec";
  the relevant limit is **~20 messages/minute per group**, and PTB does **not** auto-retry.
- `link_preview_options=LinkPreviewOptions(is_disabled=True)` on these sends (finding #10) — the
  footer's source `<a>` otherwise renders a preview card as the last thing on a long answer.

Applied at **five** sites (**G6** — v1 said four and missed one):

| Line | Site | Currently |
|------|------|-----------|
| 229/231 | `_on_message` — judging reply | `try` HTML / `except` plain |
| **258/262** | `_on_message` — conversation reply | **the observed failure** |
| 413/417 | `_on_feedback` — 🔄 retry reply | same shape, same latent bug |
| 527/529 | `_on_web_search` — live njit.edu reply | same shape, same latent bug |
| **521** | `_on_web_search` — `_live_links_text(live.urls)` | **missed in v1.** `_live_links_text` (`message_handler.py:85-90`) is unbounded in `len(urls)`; nothing in the Brave path caps the list |

**Audit of every other send site (so G6 is provably complete, not just asserted):** `:516`
(`LIVE_NOT_FOUND_MSG`), `:319`, `:348`, `:388`, `:455/:461`, `:549`, `:569/:576/:584` are all fixed
short strings — out of scope. `:590` is a photo caption already capped by `content[:80]`, well
under `MessageLimit.CAPTION_LENGTH` (1024). Callback data is already guarded at `:96-101`.

### 3.4 Global error handler (**G5**)

In `start()` / app setup:

```python
self.app.add_error_handler(self._on_error)

async def _on_error(self, update, context) -> None:
    logger.error("Telegram handler error", exc_info=context.error)
    # Gated on TelegramError: a post-delivery failure (e.g. db.log_feedback_rating raising)
    # must NOT apologize for an answer the user already received.
    if not isinstance(context.error, TelegramError):
        return
    msg = getattr(update, "effective_message", None)
    if msg is not None:
        with contextlib.suppress(Exception):
            await msg.reply_text(
                "Something went wrong sending that answer. Please try asking again."
            )
```

Cheap, and it converts every future silent handler crash into a visible degrade.

Verified for PTB 22.7: `Application.add_error_handler(callback, block=True)` exists and the
`(update: object, context)` signature matches. **No loop risk** — `Application.process_error`
documents that exceptions raised *by* an error handler are only logged; the `suppress` is
belt-and-suspenders. Note `_on_web_search` (`:507`) and `_on_feedback` (`:343`) already send their
own error messages, so those paths will double-apologize; accepted (both are already-broken paths,
and a duplicate apology beats silence).

### 3.5 Part B — Discord stops truncating (SHIPS in this change, as its own commit)

`bot/commands/chat.py:130` does `body[:4093] + "..."`. Under the verbatim hard line, silently
dropping ~3,000 characters of a faculty answer is the same defect class, just quieter.

**Fable's ruling:** ship it in this change (same spec, same review — the one-change gate is
satisfied), as a **separate commit** so it stays independently revertible, since it touches a
working-if-lossy path.

**Corrected for Discord's real limits (finding #9 — v1 was under-specified).** v1 said "follow-up
embeds", which collides with three caps: embed description 4,096, **total embed payload 6,000**,
**10 embeds per message**, and `message.reply(embeds=[...])` cannot bind a `View` to a specific
embed. So:
- Split the body with the **same** `split_plain` helper.
- Send each chunk as its **own follow-up message** with a single embed (not N embeds in one
  message) — this sidesteps the 6,000 total and the 10-embed cap entirely.
- Follow-ups use `mention_author=False` and do not re-ping.
- Footer + `FeedbackView` on the **last** message only (mirror of G3).
- Fix the ordering bug at `chat.py:127`: `source_link` is appended to `body` *before* the length
  check, so today the source link itself is part of what gets truncated away.

### 3.6 Explicitly NOT doing

- Not extracting/re-using `groupme_connector._chunk` in this change. GroupMe works today; sharing
  the helper means touching a healthy path. `msg_split.split_plain` is written to subsume it, and
  the GroupMe migration is a follow-up if the owner wants it.
- Not touching the retriever, `entity_card`, `deterministic_suffix`, or any answer content.

---

## 4. Test plan (TDD — tests first)

New `bot/tests/test_msg_split.py`:
1. Text under the limit → exactly one chunk, byte-identical to the input.
2. 7,100-char text → every chunk ≤ 4,096 **UTF-16 units**, asserted on **both the plain and the
   RENDERED** form. *(v1's test 2 checked only plain length and would have passed the broken
   algorithm.)* Content invariant: no non-whitespace character lost.
3. **The reviewer's counter-example, as a regression test:** `"&" * 4000` at the real budget →
   every chunk's `_tg_html(...)` render ≤ 4,096 UTF-16 units. This is the exact input that produced
   19,500 under v1.
4. Emoji-heavy text near the boundary → UTF-16 counting keeps it under (a char-counting impl fails).
5. Boundary ladder prefers `\n\n`, then `\n`, then space; a single 5,000-char unbroken token still
   splits (hard cut) rather than looping forever. **Termination:** ≥1 char of progress per iteration.
6. **No empty chunk is ever emitted** (would be a Telegram 400 `message text is empty`).
7. **Masked-link atomicity** (Fable): a `[label](url)` positioned to straddle a boundary survives
   intact in one chunk — no raw half-URL.
8. Each rendered chunk is tag-balanced, checked by **parsing with `html.parser.HTMLParser` and
   asserting well-formed nesting** — not by counting tags. *(v1's count-based test 5 passes on the
   crossed-emphasis case `<b>bold <i>ital</b> rest</i>` that Telegram rejects.)*
9. **Perf:** splitting a 7,100-char answer completes in < 50 ms. *(v1's implied char-granularity
   render-in-the-loop measured **1.07 s** of pure CPU — and PTB's
   `max_concurrent_updates=1` default means that is 1.07 s during which the bot answers nobody.)*

Extend `bot/tests/test_telegram_connector.py`:
10. **Regression, the actual bug** — a 7,100-char `resp.text` produces ≥2 `reply_text` calls, all
    succeeding; a fake bot that raises `BadRequest("Message is too long")` on any >4,096 payload
    sees zero such payloads.
11. Keyboard is attached to the **last** call only; footer appears in the **last** chunk only.
12. HTML parse failure on chunk 2 falls back to plain text **for that chunk**, and the fallback
    payload is also ≤ limit (D2 regression).
13. **`RetryAfter` on chunk 2** → slept and retried once, and the full answer still lands (finding
    #6 — the residual silent-drop).
14. Short answer → exactly one `reply_text`, keyboard attached — proves no behavior change for the
    99% case.

**Fixture-compatibility note (finding #12):** existing tests at `test_telegram_connector.py:73`,
`:84`, `:139`, `:150` read `reply_text.call_args[0][0]`, i.e. the **last** call — so a multi-chunk
answer would silently move what they assert against. All four exercise short answers, which test 14
pins to exactly one call, so they remain valid. Confirmed rather than assumed.

Live verification after build: ask Telegram "who is Taro Narahara" and confirm a complete 2-message
answer with working 👍/👎/🔄.

Per `feedback_grow_correctness_suite`: add "who is Taro Narahara" to `eval/questions.txt`.

---

## 5. Risk

| Risk | Mitigation |
|------|-----------|
| Chunking breaks the common short answer | Test 9 pins single-message + keyboard behavior; helper returns `[text]` unchanged under the limit |
| Feedback buttons land on the wrong message | Test 7; `_register_pending` still stores the FULL `resp.text` (unchanged), so 🔄 and ownership checks are unaffected |
| Markdown span split across chunks renders literal `**` | Accepted, cosmetic; paragraph-first boundaries make it rare |
| Rate limiting on multi-chunk sends | **Corrected:** the binding limit is ~20 msg/min per group, not 30/sec, and PTB does not auto-retry → explicit `RetryAfter` catch + one retry (§3.3), pinned by test 13 |
| Last chunk alone fails → partial answer, no buttons, `question_id` already registered | Logged loudly by the new error handler; the user sees an apology instead of silence. Noted rather than engineered around — the pre-existing single-message failure has the same shape |
| Crossed markdown emphasis renders invalid HTML | Pre-existing `_tg_html` defect, not introduced here; caught by the per-chunk plain fallback (G4). Recorded, not fixed in this change |

**Backout:** revert the commit + `bash scripts/restart.sh`. No DB writes, no schema change, no
flag needed (this is a pure bug fix restoring intended behavior — a flag would leave the broken
path reachable).

---

## 6. Goals checklist (to be filled at PR time)

| Goal | Status |
|------|--------|
| G1 long answer delivered in full | ✅ `_reply_chunked`; `RetryAfter` retry closes the residual drop. Tests: `test_long_answer_is_split_and_every_chunk_is_accepted`, `..._content_is_not_lost`, `test_retry_after_is_retried...` |
| G2 each chunk **tag-balanced**, validity backstopped by G4 | ✅ restated after review; `test_every_rendered_chunk_is_well_nested` parses NESTING, not counts |
| G3 footer + keyboard once, on last chunk | ✅ `test_footer_and_keyboard_ride_on_the_last_chunk_only` |
| G4 plain fallback is length-safe | ✅ `test_html_parse_failure_falls_back_to_plain_text_that_also_fits` |
| G5 global error handler | ✅ `add_error_handler` at setup, gated on `TelegramError` |
| G6 **all five** send sites + full audit of the rest | ✅ 229, 258, 413, 521, 527 all routed through `_reply_chunked` |
| G7 Discord truncation (Part B) | ✅ `chat.py` splits into one embed per message; source link moved to the last chunk (it used to be truncated away) |

**Test results:** 24 new unit tests (`test_msg_split.py`) + 6 new integration tests
(`test_telegram_connector.py`) — all pass. Full `bot/tests/` run: **747 passed, 16 failed**; the
same **16 failures reproduce with this change reverted** (verified by `git stash` + re-run), so
they are pre-existing and unrelated (worldcup event-loop, departments registry, router).

**Regression caught during the build:** the first cut of the `href` fix double-escaped —
`html.escape(url, quote=True)` on a URL the outer `html.escape` had already escaped, turning `&`
into `&amp;amp;` and breaking every Scholar URL. Caught by the existing
`test_tg_html.py::test_link_url_with_ampersand_is_escaped_in_href`. Now escapes only `"`.

**Not committed. Not restarted.** Awaiting owner sign-off on the diff.

## 7. Review ledger — every finding, resolved or explicitly declined

| # | Finding | Disposition |
|---|---------|-------------|
| SE-1 | **[BLOCKER]** split algorithm emits 19,500-unit chunks (demonstrated) | **Fixed** — §3.1 rewritten in plain space + post-condition; regression test 3 |
| SE-2 | **[BLOCKER]** G2 "valid HTML" overclaimed; crossed emphasis; unescaped `href` | **Fixed** — G2 restated, test 8 parses nesting, `quote=True` on the URL |
| SE-3 | budget the wrong quantity; render-in-loop unnecessary | **Adopted** — plain-space budget; `split_rendered` collapsed into a post-condition net |
| SE-4 | O(n²) blocks the event loop (1.07 s measured) | **Fixed** — no render-in-loop; perf test 9 |
| SE-5 | `.strip()` contradicts the content invariant; empty chunk → 400 | **Fixed** — invariant restated; test 6 |
| SE-6 | `RetryAfter` silently drops the tail | **Fixed** — catch + one retry; test 13; §5 figure corrected |
| SE-7 | G6 misses a fifth site (`:521`) | **Fixed** — five sites + full audit of the remainder |
| SE-8 | hardcoded 4096 | **Fixed** — `MessageLimit.MAX_TEXT_LENGTH` |
| SE-9 | Part B under-specified for Discord's real caps | **Fixed** — one message per chunk; §3.5 |
| SE-10 | link preview on the last chunk | **Adopted** — `LinkPreviewOptions(is_disabled=True)` |
| SE-11 | note the 5-line hotfix as a ship-now option | **Surfaced to owner** — see below |
| SE-12 | existing fixtures read the *last* `call_args` | **Verified safe** — §4 note |
| F-1 | masked-link atomicity | **Adopted** — atomic spans; test 7 |
| F-2 | Part B ships, separate commit | **Adopted** |
| F-3 | justify or drop the 3900 budget | **Adopted** — derived from a measured 145-unit footer |
| F-4 | split, don't trim the card | **Adopted** — non-goal, unchanged |

**Owner decision surfaced (SE-11):** this is a *live outage* sitting behind a review gate. A 5-line
hotfix (`except BadRequest:` → split-and-send plain) could restore answers immediately, decoupled
from the full design. Offered; not taken unilaterally.
