"""Identity single-source-of-truth: renders read config, version lineage is append-one,
no drift (live model, no stale 'v2.1'), and the disambiguation guard holds."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import bot.core.identity as identity
from bot.services.intent_detector import IntentDetector, INTENT_IDENTITY


# ── config + helpers ──────────────────────────────────────────────────────────────────────
def test_current_is_kavosh_v25():
    assert identity.current()["name"] == "Kavosh"
    assert identity.version_label() == "Kavosh v2.5"


def test_lineage_is_binesh_with_corrected_date():
    lin = identity.lineage()
    assert [v["name"] for v in lin] == ["Binesh"]
    # corrected date: Kavosh persona launched 2026-06-20 (NOT the old, wrong "June 15")
    assert lin[0]["active_to"] == "2026-06-20"


def test_no_stale_v21_anywhere_in_config():
    import json
    blob = json.dumps(identity.IDENTITY) + json.dumps(identity.VERSIONS)
    assert "v2.1" not in blob
    assert "v2.1" not in identity.persona_line("granite4:tiny-h")
    assert "v2.1" not in identity.render_full("granite4:tiny-h")


# ── full render ───────────────────────────────────────────────────────────────────────────
def test_full_render_reads_config_and_live_model():
    out = identity.render_full("granite4:tiny-h")
    assert "GSA Gateway" in out
    assert "Kavosh v2.5" in out
    assert "granite4:tiny-h" in out          # LIVE model injected, not hardcoded
    assert "Binesh" in out                   # predecessor named
    assert "June 20, 2026" in out            # corrected retirement date, pretty-printed
    assert "June 15" not in out              # the old wrong date is gone
    assert "I don't know" in out             # honesty value stated explicitly


def test_full_render_model_drift_guard():
    # whatever live model is passed shows up verbatim — the render never pins a model string
    assert "some-future-model:9b" in identity.render_full("some-future-model:9b")


def test_model_less_fallback_is_short_but_versioned():
    out = identity.render_full(None)
    assert "Kavosh v2.5" in out
    assert "granite" not in out.lower()      # no model claimed when none is available


# ── focused short-circuits ────────────────────────────────────────────────────────────────
def test_focused_creator():
    out = identity.render_self("who made you?", "granite4:tiny-h")
    assert "Mohammad Dindoost" in out and "md724@njit.edu" in out
    assert "🔬" not in out                    # focused, not the full capabilities wall


def test_focused_limits_is_the_honesty_line():
    assert identity.render_self("do you make things up?", None) == identity.render_limits()


def test_focused_lineage_walks_names():
    out = identity.render_self("who came before you?", "granite4:tiny-h")
    assert "Binesh" in out
    assert "Kavosh" in out


def test_focused_infra_says_local_not_chatgpt():
    out = identity.render_self("are you chatgpt?", "granite4:tiny-h")
    assert "not a cloud service" in out and "not ChatGPT" in out
    assert "granite4:tiny-h" in out


def test_generic_who_are_you_gets_full_render():
    out = identity.render_self("who are you", "granite4:tiny-h")
    assert "🔬" in out                        # the full capabilities block


# ── append-one invariant (ship the next version = append one dict) ────────────────────────
def test_ship_next_version_is_append_one(monkeypatch):
    future = list(identity.VERSIONS)
    # flip current -> retired, append the new current
    future[-1] = {**future[-1], "status": "retired", "active_to": "2026-09-01"}
    future.append({"name": "Simorgh", "release": "v3.0", "meaning": "the phoenix",
                   "persian": "سیمرغ", "status": "current", "active_from": "2026-09-01",
                   "active_to": None, "model": None, "summary": "the next chapter"})
    monkeypatch.setattr(identity, "VERSIONS", future)
    assert identity.current()["name"] == "Simorgh"
    assert identity.version_label() == "Simorgh v3.0"
    assert [v["name"] for v in identity.lineage()] == ["Binesh", "Kavosh"]
    assert "Kavosh" in identity.render_self("who came before you?", "m")


# ── routing: new patterns land on identity; 'who is <name>' does NOT ──────────────────────
def test_new_self_patterns_route_to_identity():
    det = IntentDetector()
    for q in ("who made you", "who runs you", "what do you run on",
              "who came before you", "do you make things up", "are you chatgpt"):
        assert det.detect(q)[0] == INTENT_IDENTITY, q


def test_who_is_faculty_does_not_route_to_identity():
    det = IntentDetector()
    for q in ("who is Koutis", "who is Guiling Wang", "who is the dean of YWCC"):
        assert det.detect(q)[0] != INTENT_IDENTITY, q


def test_limits_questions_do_not_hijack_gsa_policy(monkeypatch):
    # senior-eng Finding A: bare "limits" must NOT capture real GSA policy questions
    det = IntentDetector()
    for q in ("what are your limits on reimbursement",
              "do you have any limits on how much funding I can request",
              "what are the limits on travel awards",
              # the \b anchor also fixed a broader pre-existing over-match:
              "who are your officers", "what are your office hours",
              # Fable Finding: the NEW creator/operator patterns needed \b too —
              # "…you" must not match inside "…your":
              "who runs your events", "who made your website",
              "who operates your knowledge base", "who came before your president"):
        assert det.detect(q)[0] != INTENT_IDENTITY, q


def test_reliability_questions_route_to_identity():
    det = IntentDetector()
    for q in ("do you make things up", "can i trust you", "are you accurate",
              "what are your limitations"):
        assert det.detect(q)[0] == INTENT_IDENTITY, q


def test_render_self_creator_requires_you_anchor():
    # senior-eng Finding B: keyword dispatch must not fire on a non-self "who runs X"
    out = identity.render_self("who runs the writing center", "granite4:tiny-h")
    assert "created by" not in out          # not the creator answer
    assert "🔬" in out                       # falls through to the full render


def test_current_raises_clear_error_if_no_current(monkeypatch):
    # senior-eng Finding D: misconfigured lineage fails loudly with a helpful message
    monkeypatch.setattr(identity, "VERSIONS",
                        [{**identity.VERSIONS[0], "status": "retired"}])
    try:
        identity.current()
        assert False, "expected ValueError"
    except ValueError as e:
        assert "current" in str(e)
