from __future__ import annotations
from eval.processing_debt.types import OracleAnswer, Nugget

_SCHEMA = {"type": "object", "properties": {"nuggets": {"type": "array", "items": {
    "type": "object",
    "properties": {"text": {"type": "string"}, "vital": {"type": "boolean"}},
    "required": ["text", "vital"]}}}, "required": ["nuggets"]}
_SYSTEM = ("Decompose the ANSWER to the QUESTION into atomic facts (each a single, self-contained, "
           "verifiable statement — no pronouns, no conjunctions). For each, set vital=true if it is "
           "essential to correctly answering the QUESTION, vital=false if it is helpful-but-incidental "
           "detail. Copy facts faithfully; do not add facts not present in the ANSWER.")

def _default_gen(system, prompt, schema):
    from bot.services.ollama_client import generate_json_sync
    return generate_json_sync(system, prompt, schema, model="granite4:tiny-h",
                              timeout=45.0, num_predict=768)

def nuggetize(oracle: OracleAnswer, *, gen=None) -> list[Nugget]:
    gen = gen or _default_gen
    prompt = f"QUESTION:\n{oracle.question}\n\nANSWER:\n{oracle.answer}"
    out = gen(_SYSTEM, prompt, _SCHEMA)
    if not out or not isinstance(out.get("nuggets"), list):
        return []
    res = []
    for n in out["nuggets"]:
        t = (n.get("text") or "").strip()
        if t:
            res.append(Nugget(text=t, vital=bool(n.get("vital"))))
    return res
