from __future__ import annotations
import hashlib, json, re, urllib.request, urllib.error
from pathlib import Path
from eval.processing_debt.types import OracleAnswer, OracleCitation

ENDPOINT = "https://api.search.brave.com/res/v1/chat/completions"
MAX_LIVE_CALLS = 200
_live_calls = 0
_CIT_RE = re.compile(r"<citation>(.*?)</citation>", re.S)


def reset_spend() -> None:
    global _live_calls
    _live_calls = 0


def _read_keys() -> list[str]:
    found = {}
    for line in open(Path(__file__).resolve().parents[2] / ".env"):
        line = line.strip()
        if "=" in line and (line.startswith("BRAVE_ANSWERS_API_KEY=")
                            or line.startswith("BRAVE_ANSWERS_API_KEY_")):
            k, v = line.split("=", 1)
            found[k] = v.strip()
    if not found:
        raise RuntimeError("no BRAVE_ANSWERS_API_KEY* in .env")
    def _order(k): return 0 if k == "BRAVE_ANSWERS_API_KEY" else int(k.rsplit("_", 1)[1])
    return [found[k] for k in sorted(found, key=_order)]


def _real_http(url: str, body: bytes, headers: dict) -> str:
    """POST a STREAMING grounded-answer request; accumulate and return the full content string
    (which carries inline <citation>{...}</citation> tags). Advanced params (citations) require stream=true."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    parts = []
    with urllib.request.urlopen(req, timeout=120) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except Exception:
                continue
            delta = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content", "") or ""
            parts.append(delta)
    return "".join(parts)


def _parse(question: str, content: str) -> OracleAnswer:
    """Extract inline <citation> tags (url + snippet) and return the answer with the tags stripped."""
    cites = []
    for m in _CIT_RE.findall(content or ""):
        try:
            d = json.loads(m)
        except Exception:
            continue
        if d.get("url"):
            snip = d.get("snippet")
            cites.append(OracleCitation(url=d["url"], title=(snip or d.get("title")), snippet=snip))
    answer = _CIT_RE.sub("", content or "").strip()
    return OracleAnswer(question=question, answer=answer, citations=cites, raw={"content": content})


def ask_oracle(question: str, *, cache_dir: str = "eval/processing_debt/.cache/oracle",
               http=None, keys=None, max_live: int | None = None) -> OracleAnswer:
    global _live_calls
    http = http or _real_http
    keys = keys if keys is not None else _read_keys()
    cap = MAX_LIVE_CALLS if max_live is None else max_live
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(question.strip().lower().encode()).hexdigest()[:16]
    cache_file = Path(cache_dir) / f"{key}.json"
    if cache_file.exists():
        return _parse(question, json.loads(cache_file.read_text()).get("content", ""))
    if _live_calls >= cap:
        raise RuntimeError(f"MAX_LIVE_CALLS ({cap}) reached — refusing further live oracle calls")
    body = json.dumps({
        "model": "brave", "stream": True, "enable_citations": True,
        "country": "us", "language": "en",
        "messages": [{"role": "user", "content": question}],
    }).encode()
    last_err = None
    for tok in keys:
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "X-Subscription-Token": tok}
        try:
            content = http(ENDPOINT, body, headers)
        except urllib.error.HTTPError as e:
            if e.code in (402, 429):     # credit/quota exhausted on THIS key → try next
                last_err = e
                continue
            raise
        _live_calls += 1
        cache_file.write_text(json.dumps({"content": content}))
        return _parse(question, content)
    raise RuntimeError(f"all Brave Answers keys exhausted (last error: {last_err})")
