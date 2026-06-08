"""One-shot: post the Curaçao fact to Discord #world-cup-2026 and Telegram channel."""

import asyncio
import os
import sys

import aiohttp
from dotenv import load_dotenv
from telegram import Bot
from telegram.error import TelegramError

load_dotenv()

POST_TEXT = """\
🏝️ The smallest nation EVER at a World Cup
is smaller than Newark, NJ

Curaçao — population 156,000 — qualified
for the 2026 World Cup. For context, Newark
has ~311,000 people. That means YOUR city
is literally bigger than an entire World Cup
nation.

Previously the smallest qualifier was Iceland
in 2018 with 350,000 people. Curaçao has less
than half that. The US roster alone has more
people in some cities than this entire country.

🤔 Who are you rooting for — the giant or
the underdog?

🏝️ = Curaçao all day
🇺🇸 = USA obviously
🤯 = Still processing this"""

REACTIONS = ["🏝️", "🇺🇸", "🤯"]

DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN", "")
DISCORD_GUILD   = os.getenv("DISCORD_GUILD_ID", "")
FOOTBALL_CHANNEL = os.getenv("FOOTBALL_CHANNEL", "world-cup-2026")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_TARGET = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHANNEL_ID", "")

DISCORD_API = "https://discord.com/api/v10"
HEADERS = {"Authorization": f"Bot {DISCORD_TOKEN}", "Content-Type": "application/json"}


async def find_discord_channel(session: aiohttp.ClientSession) -> str | None:
    async with session.get(f"{DISCORD_API}/guilds/{DISCORD_GUILD}/channels", headers=HEADERS) as r:
        if r.status != 200:
            print(f"  ✗ Could not fetch channels: {r.status}")
            return None
        channels = await r.json()
    for ch in channels:
        if ch.get("name") == FOOTBALL_CHANNEL:
            return ch["id"]
    print(f"  ✗ Channel #{FOOTBALL_CHANNEL} not found")
    return None


async def post_discord(session: aiohttp.ClientSession) -> bool:
    print(f"Discord → #{FOOTBALL_CHANNEL} ...")
    channel_id = await find_discord_channel(session)
    if not channel_id:
        return False

    async with session.post(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        headers=HEADERS,
        json={"content": POST_TEXT},
    ) as r:
        if r.status not in (200, 201):
            print(f"  ✗ Post failed: {r.status} {await r.text()}")
            return False
        msg = await r.json()

    msg_id = msg["id"]
    print(f"  ✓ Message posted (id {msg_id})")

    for emoji in REACTIONS:
        encoded = aiohttp.helpers.quote(emoji, safe="")
        async with session.put(
            f"{DISCORD_API}/channels/{channel_id}/messages/{msg_id}/reactions/{encoded}/@me",
            headers={k: v for k, v in HEADERS.items() if k != "Content-Type"},
        ) as r:
            if r.status == 204:
                print(f"  ✓ Reaction {emoji} added")
            else:
                print(f"  ✗ Reaction {emoji} failed: {r.status}")
        await asyncio.sleep(0.5)  # avoid reaction rate limit

    return True


async def post_telegram() -> bool:
    print(f"Telegram → {TELEGRAM_TARGET} ...")
    if not TELEGRAM_TOKEN or not TELEGRAM_TARGET:
        print("  ✗ TELEGRAM_TOKEN or target chat ID not set")
        return False
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        await bot.send_message(chat_id=TELEGRAM_TARGET, text=POST_TEXT)
        print("  ✓ Message posted")
        return True
    except TelegramError as e:
        print(f"  ✗ {e}")
        return False


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        d_ok = await post_discord(session)
    t_ok = await post_telegram()

    print()
    if d_ok and t_ok:
        print("✅ Posted to both Discord and Telegram.")
    elif d_ok:
        print("⚠️  Posted to Discord only. Telegram failed.")
    elif t_ok:
        print("⚠️  Posted to Telegram only. Discord failed.")
    else:
        print("❌ Both failed.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
