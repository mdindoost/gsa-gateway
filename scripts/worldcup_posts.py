"""Scheduled World Cup daily posts — run by cron at 9 AM ET.

Looks up today's date, finds a matching post, and sends it to
Discord #world-cup-2026 (with reactions) and Telegram channel.

Add new entries to POSTS to schedule future posts.
"""

import asyncio
import sys
from datetime import date

import aiohttp
from dotenv import load_dotenv

load_dotenv()

import os
from telegram import Bot
from telegram.error import TelegramError

# ── Scheduled posts ───────────────────────────────────────────────────────────
# date(YYYY, M, D): {"text": "...", "reactions": ["emoji", ...]}

POSTS = {
    date(2026, 6, 9): {
        "text": (
            "💰 The World Cup winner takes home $50 million\n"
            "— 23x more than the 1982 champions\n\n"
            "FIFA has allocated $655 million total in prize\n"
            "money for 2026. Champions get $50M. Runners-up\n"
            "get $33M. Even teams eliminated in the group\n"
            "stage walk away with millions.\n\n"
            "For comparison: Italy won the 1982 World Cup\n"
            "and received $2.2 million. Same trophy.\n"
            "23x less money.\n\n"
            "Every single tournament since 1982 the prize\n"
            "has increased without exception.\n\n"
            "🏆 = Worth it\n"
            "💸 = Where do I sign up\n"
            "📈 = Compound interest but make it football"
        ),
        "reactions": ["🏆", "💸", "📈"],
    },
    date(2026, 6, 10): {
        "text": (
            "🤖 Every player at this World Cup has been\n"
            "3D-scanned into an AI avatar\n\n"
            "Every player. All 736 of them. Digital twins\n"
            "of all 16 stadiums exist and update in real\n"
            "time. The semi-automated offside system\n"
            "visualizes exact moments in 3D.\n\n"
            "The tournament will generate 90 petabytes of\n"
            "raw data — 45x more than Qatar 2022. Add AI\n"
            "modeling, streaming, and social media and it\n"
            "hits 2 exabytes. Bank of America predicts the\n"
            "FINAL MATCH ALONE will consume 7% of global\n"
            "internet traffic.\n\n"
            "FIFA president literally called it\n"
            "\"104 Super Bowls in one month.\"\n\n"
            "As CS students — how would YOU architect\n"
            "the data pipeline for this? 🤔\n\n"
            "💾 = Watching for the football\n"
            "💻 = Watching for the tech\n"
            "🔥 = Both obviously"
        ),
        "reactions": ["💾", "💻", "🔥"],
    },
}

# ── Config ────────────────────────────────────────────────────────────────────

DISCORD_TOKEN    = os.getenv("DISCORD_TOKEN", "")
DISCORD_GUILD    = os.getenv("DISCORD_GUILD_ID", "")
FOOTBALL_CHANNEL = os.getenv("FOOTBALL_CHANNEL", "world-cup-2026")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_TARGET  = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHANNEL_ID", "")

DISCORD_API = "https://discord.com/api/v10"
HEADERS = {"Authorization": f"Bot {DISCORD_TOKEN}", "Content-Type": "application/json"}


async def find_channel(session: aiohttp.ClientSession) -> str | None:
    async with session.get(
        f"{DISCORD_API}/guilds/{DISCORD_GUILD}/channels", headers=HEADERS
    ) as r:
        if r.status != 200:
            print(f"  ✗ Could not fetch channels: {r.status}")
            return None
        channels = await r.json()
    for ch in channels:
        if ch.get("name") == FOOTBALL_CHANNEL:
            return ch["id"]
    print(f"  ✗ Channel #{FOOTBALL_CHANNEL} not found")
    return None


async def post_discord(session: aiohttp.ClientSession, text: str, reactions: list[str]) -> bool:
    print(f"Discord → #{FOOTBALL_CHANNEL} ...")
    channel_id = await find_channel(session)
    if not channel_id:
        return False

    async with session.post(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        headers=HEADERS,
        json={"content": text},
    ) as r:
        if r.status not in (200, 201):
            print(f"  ✗ Post failed: {r.status} {await r.text()}")
            return False
        msg = await r.json()

    msg_id = msg["id"]
    print(f"  ✓ Message posted (id {msg_id})")

    for emoji in reactions:
        encoded = aiohttp.helpers.quote(emoji, safe="")
        async with session.put(
            f"{DISCORD_API}/channels/{channel_id}/messages/{msg_id}/reactions/{encoded}/@me",
            headers={k: v for k, v in HEADERS.items() if k != "Content-Type"},
        ) as r:
            if r.status == 204:
                print(f"  ✓ Reaction {emoji} added")
            else:
                print(f"  ✗ Reaction {emoji} failed: {r.status}")
        await asyncio.sleep(0.5)

    return True


async def post_telegram(text: str) -> bool:
    print(f"Telegram → {TELEGRAM_TARGET} ...")
    if not TELEGRAM_TOKEN or not TELEGRAM_TARGET:
        print("  ✗ TELEGRAM_TOKEN or target not set")
        return False
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        await bot.send_message(chat_id=TELEGRAM_TARGET, text=text)
        print("  ✓ Message posted")
        return True
    except TelegramError as e:
        print(f"  ✗ {e}")
        return False


async def main() -> None:
    today = date.today()
    post = POSTS.get(today)

    if not post:
        print(f"No post scheduled for {today}. Nothing to do.")
        return

    print(f"Posting scheduled content for {today} ...")
    async with aiohttp.ClientSession() as session:
        d_ok = await post_discord(session, post["text"], post["reactions"])
    t_ok = await post_telegram(post["text"])

    print()
    if d_ok and t_ok:
        print("✅ Posted to both Discord and Telegram.")
    elif d_ok:
        print("⚠️  Discord only. Telegram failed.")
    elif t_ok:
        print("⚠️  Telegram only. Discord failed.")
    else:
        print("❌ Both failed.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
