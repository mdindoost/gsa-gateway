#!/usr/bin/env python
"""Throwaway eval harness: run 100 questions through the REAL bot answer pipeline
(message_handler.handle → structured router → v2 retriever → llama3.1), exactly as a
Telegram user would hit it. Writes one JSON line per question to eval_results.jsonl.

50 KB-grounded questions (content we should be able to answer) + 50 cold "any student"
questions (no assumption they're in the KB). Each question uses a unique user_id so the
per-user rate limiter and conversation memory don't interfere (simulates 100 students).

Run: .venv/bin/python scripts/_eval_kb_100.py [--limit N] [--offset N]
Cleanup of logged test questions is handled by the caller via the printed id watermark.
"""
from __future__ import annotations
import argparse, asyncio, json, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bot.config import config
from bot.services.database import Database
from bot.services.knowledge_base import KnowledgeBase
from bot.services.moderation import RateLimiter
from bot.core.message_handler import MessageRequest

# ── 50 KB-grounded questions (constitution / club finance / travel / PhD / resources) ──
KB_Q = [
    "What are the duties of the GSA Vice President for Academic Affairs?",
    "Who chairs the GSA General Assembly meetings?",
    "What are the six GSA Executive Board positions?",
    "What is the minimum GPA to run for a GSA Executive Board position?",
    "How is the GSA Vice President for Academic Affairs selected?",
    "What are the duties of the GSA President?",
    "What does the GSA Vice President of Finance do?",
    "What are the responsibilities of the GSA Vice President of Communications?",
    "Who can impeach a GSA officer and what vote is needed?",
    "What are the grounds for impeachment of a GSA member?",
    "How many terms can someone serve in one GSA officer position?",
    "When do new GSA officers assume their roles?",
    "What is the quorum rule for the GSA General Assembly?",
    "What vote is required to pass a motion in the GSA General Assembly?",
    "How are amendments to the GSA constitution adopted?",
    "Who are the GSA's two advisors and which offices are they from?",
    "What does the GSA Finance Committee do?",
    "How is the GSA social and cultural operating budget allocated by percentage?",
    "What is the maximum a club event can cost relative to its academic budget?",
    "What is the per-person food cost limit for a club event of 25 students?",
    "How many events must a graduate club hold on campus per semester?",
    "What is the penalty for a late club budget request?",
    "How much can a graduate club receive from a conference/competition grant?",
    "How much is an asset grant for a graduate club?",
    "What percentage of a club's budget can be spent on prizes?",
    "What happens to a club on its 2nd financial bylaw offense?",
    "Can a club use petty cash reimbursement?",
    "What is the maximum GSA travel award per fiscal year?",
    "When does the GSA fiscal year run?",
    "How many days after travel must I submit the Chrome River Expense Report?",
    "Are AirBNB stays reimbursable under the GSA travel award?",
    "How far in advance must I submit a Chrome River Pre-Approval for travel?",
    "Are online conferences eligible for the GSA travel award?",
    "What is the IRS mileage rate used for GSA travel reimbursement?",
    "What documents are required for the Chrome River Expense Report?",
    "Does external funding disqualify me from a GSA travel award?",
    "How many course credits does a CS PhD student with a Master's need?",
    "What is the qualifying exam requirement for the CS PhD?",
    "Who sits on the CS PhD Qualifying Exam Committee?",
    "What cumulative GPA must a CS PhD student maintain?",
    "What are the program milestones for the CS PhD?",
    "What is CS 791 and who must enroll in it?",
    "How long does the Informatics PhD program take?",
    "Is the Informatics PhD funded?",
    "What are the learning outcomes of the Informatics PhD?",
    "Where is the GSA office located and what are its hours?",
    "How do I contact the GSA?",
    "What is the MMI Workshop?",
    "What travel-related resources does the GSA offer?",
    "What is the GSA Three Minute Research Presentation (3MRP)?",
]

# ── 50 cold "any grad student" questions (mix of in-scope and out-of-scope) ──
COLD_Q = [
    "How do I apply for a travel grant to present at a conference?",
    "Who do I talk to about academic issues as a grad student?",
    "Is there funding available for PhD students?",
    "How do I start a graduate club at NJIT?",
    "How much money can my club get from the GSA?",
    "What mental health resources are available to grad students?",
    "How do I get involved with the GSA?",
    "When are the GSA meetings?",
    "Can I get reimbursed for a hotel at a conference?",
    "What's the GPA I need to keep as a PhD student?",
    "How do I become a department representative?",
    "What happens if my club overspends its budget?",
    "Who is the current GSA president?",
    "How do I qualify as a PhD candidate in computer science?",
    "Is there a tax help service for international students?",
    "How do I get a NJIT parking permit?",
    "When is spring break this semester?",
    "How do I pay my tuition bill?",
    "What are the dining hall hours?",
    "How do I drop a class?",
    "What's the wifi password on campus?",
    "Where is the campus gym and when is it open?",
    "How do I get a student ID card?",
    "Is there a shuttle from NJIT to New York City?",
    "How do I waive the student health insurance?",
    "What time does the library close tonight?",
    "How do I reset my NJIT password?",
    "Where can I find on-campus housing?",
    "How do I register for next semester's classes?",
    "What's the deadline to add a course?",
    "Can I get a travel award for a conference where I'm not presenting?",
    "How do I report a club bylaw violation?",
    "What's the per diem for meals on conference travel?",
    "Can undergraduates join the GSA?",
    "How do I co-host an event with another organization?",
    "What is the deadline to submit my club's budget?",
    "Are gift cards allowed as club prizes and what's the limit?",
    "How many speakers were at the MMI workshop?",
    "Who advises the GSA on finances?",
    "What's the difference between the President and VP of Academic Affairs?",
    "Can a part-time student be a GSA officer?",
    "How do I get a research assistantship?",
    "What's the stipend for a funded PhD student?",
    "Where do I submit my conference receipts?",
    "Is there a writing center for grad students?",
    "How do I appeal a grade?",
    "What graduate clubs exist at NJIT?",
    "Can I get money for buying a laptop for research?",
    "How do I contact the Office of Graduate Studies?",
    "What is NJIT's policy on academic integrity?",
]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--out", default=str(REPO / "eval_results.jsonl"))
    args = ap.parse_args()

    questions = [("kb", q) for q in KB_Q] + [("cold", q) for q in COLD_Q]
    questions = questions[args.offset: args.offset + args.limit]

    db = Database(config.database_path); db.connect()
    db.init_tables(); db.migrate_rag_columns()
    kb = KnowledgeBase(data_dir=config.data_dir); kb.load()
    # rate limit effectively off; unique user per Q also avoids it
    rl = RateLimiter(max_calls=100000, period_seconds=1)

    watermark = db.conn.execute("SELECT COALESCE(MAX(id),0) FROM questions").fetchone()[0]
    print(f"question-id watermark before run: {watermark}", flush=True)

    from bot.core.assistant import build_assistant
    asst = await build_assistant(config, db, kb, rl)
    handler = asst.message_handler

    out = open(args.out, "a", encoding="utf-8")
    for i, (cat, q) in enumerate(questions):
        uid = f"evalbot-{args.offset + i}"
        t0 = time.time()
        try:
            resp = await handler.handle(MessageRequest(user_id=uid, text=q, platform="telegram"))
            rec = {"i": args.offset + i, "cat": cat, "q": q,
                   "answer": (resp.text or "").strip(),
                   "source_note": resp.source_note, "used_ai": resp.used_ai,
                   "ollama_failed": resp.ollama_failed, "secs": round(time.time() - t0, 1)}
        except Exception as e:
            rec = {"i": args.offset + i, "cat": cat, "q": q, "error": repr(e),
                   "secs": round(time.time() - t0, 1)}
        out.write(json.dumps(rec) + "\n"); out.flush()
        print(f"[{args.offset+i+1}/{args.offset+len(questions)}] ({cat}) {rec.get('secs')}s  {q[:55]}", flush=True)

    out.close()
    if asst.embedder: await asst.embedder.close()
    if asst.ollama: await asst.ollama.close()
    db.close()
    print("DONE", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
