import json
import os
import subprocess


class RateLimitError(Exception):
    """Raised when the Codex teacher hits a ChatGPT usage-window / rate limit."""
    pass


def detect_rate_limit(returncode: int, stdout: str, stderr: str) -> bool:
    """Return True when stdout+stderr signal a rate-limit / usage-window error."""
    _SIGNALS = (
        "rate limit", "rate_limit", "usage limit", "quota", "429",
        "too many requests", "try again later", "resets",
    )
    combined = (stdout + stderr).lower()
    return any(s in combined for s in _SIGNALS)


def parse_codex_output(jsonl_text: str) -> dict:
    final = None
    for line in jsonl_text.splitlines():
        line = line.strip()
        if not line:
            continue
        o = json.loads(line)
        it = o.get("item", {})
        if it.get("type") == "agent_message" and o.get("type") == "item.completed":
            final = it.get("text")
    if not final:
        raise ValueError("no agent_message in codex output")
    try:
        return json.loads(final)
    except json.JSONDecodeError as e:
        raise ValueError(f"agent_message not JSON: {e}")


def extract_error_message(jsonl_text: str) -> str | None:
    """Return the message from a codex ``error`` / ``turn.failed`` event.

    The usage-window signal is delivered as a STRUCTURED stdout event, e.g.
    {"type":"error","message":"You've hit your usage limit ... try again at
    10:06 PM."} plus a paired {"type":"turn.failed","error":{"message":...}} —
    NOT via stderr (which stays empty). Returns the last such message, or None.
    Parsing only the structured event (not raw answer prose) means we never
    misclassify an answer that merely mentions "quota"/"429"/"resets".
    """
    msg = None
    for line in jsonl_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("type") == "error" and o.get("message"):
            msg = o["message"]
        elif o.get("type") == "turn.failed":
            err = o.get("error") or {}
            if isinstance(err, dict) and err.get("message"):
                msg = err["message"]
    return msg


def decide(stdout: str, stderr: str, returncode: int) -> dict:
    """Post-subprocess decision (pure, injectable-testable).

    1. Try to parse a valid agent_message from stdout.
       If parsing succeeds, return the parsed dict immediately — a successful
       answer is NEVER treated as rate-limited regardless of its text content.
    2. Only on parse failure: check ``stderr`` (NOT stdout) for rate-limit
       signals.  If found, raise RateLimitError.
    3. Otherwise re-raise the original parse error so genuine garbage still
       surfaces as ValueError.
    """
    try:
        return parse_codex_output(stdout)
    except ValueError:
        # The usage-window / rate-limit signal arrives as a STRUCTURED stdout
        # error event (not stderr, not free answer prose) — check that event's
        # message first so an exhausted window pauses cleanly instead of being
        # mis-recorded as a per-question failure.
        err_msg = extract_error_message(stdout)
        if err_msg and detect_rate_limit(returncode, "", err_msg):
            raise RateLimitError(f"Codex usage/rate limit: {err_msg[:300]}")
        # Fallback: stderr signal (original behavior, kept for robustness).
        if detect_rate_limit(returncode, "", stderr):
            raise RateLimitError(
                f"Codex rate/usage limit (rc={returncode}): {stderr[:200]}"
            )
        raise  # re-raise original parse error


SCHEMA = os.path.join(os.path.dirname(__file__), "answer_schema.json")
CODEX_TIMEOUT = 180


def run_codex(prompt: str, model: str | None = None, schema_path: str = SCHEMA) -> dict:
    cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "-s", "read-only",
           "--output-schema", schema_path]
    if model:
        cmd += ["-m", model]
    cmd.append(prompt)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=CODEX_TIMEOUT)
    return decide(result.stdout, result.stderr, result.returncode)
