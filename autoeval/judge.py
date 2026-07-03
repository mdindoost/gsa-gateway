from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path("/home/md724/gsa-gateway")))

_SYS = ("You compare a bot ANSWER to KNOWN FACTS about an item. Reply with ONE word: "
        "CORRECT (answer matches the facts), PARTIAL (partially right/incomplete), or "
        "WRONG (contradicts or unrelated). One word only.")

def parse_verdict(raw: str) -> tuple[str, float]:
    up = (raw or "").upper()
    for word, conf in (("CORRECT", 0.9), ("PARTIAL", 0.6), ("WRONG", 0.9)):
        if word in up:
            return word.lower(), conf
    return "error", 0.0

async def judge(question: str, answer: str, ground_truth: str) -> tuple[str, float]:
    """Soft signal ONLY. Never part of deterministic pass/fail."""
    from bot.services.ollama_client import OllamaClient
    client = OllamaClient()
    prompt = f"KNOWN FACTS:\n{ground_truth}\n\nQUESTION: {question}\nANSWER: {answer}\n\nVerdict:"
    try:
        raw = await client.generate(prompt=prompt, system=_SYS)
    except Exception:
        return "error", 0.0
    finally:
        try: await client.close()
        except Exception: pass
    return parse_verdict(raw)
