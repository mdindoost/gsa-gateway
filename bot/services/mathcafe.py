"""MathCafe service — daily facts/puzzles posted to #gsa-mathcafe at 9 AM NJ time."""

import asyncio
import datetime
import logging
from pathlib import Path
from typing import Optional

import discord
import yaml

logger = logging.getLogger(__name__)

MATHCAFE_TIMEZONE = "America/New_York"
MATHCAFE_HOUR = 9
MATHCAFE_MINUTE = 0
FACTS_FILE = Path("bot/data/mathcafe/facts.yml")
IMAGES_DIR = Path("bot/data/mathcafe/images")
MATHCAFE_COLOR = 0x8B4513  # warm brown like coffee


class MathCafeService:
    """Loads, posts, and tracks MathCafe daily facts."""

    def __init__(
        self,
        bot,
        facts_file: Optional[Path] = None,
        images_dir: Optional[Path] = None,
    ) -> None:
        self.bot = bot
        self._facts_file = facts_file or FACTS_FILE
        self._images_dir = images_dir or IMAGES_DIR
        self.facts: list[dict] = []
        self.current_index: int = 0
        self.load_facts()

    # ── Load / Save ───────────────────────────────────────────────────────────

    def load_facts(self) -> None:
        try:
            with open(self._facts_file, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except FileNotFoundError:
            logger.warning("MathCafe facts file not found: %s", self._facts_file)
            return

        self.facts = data.get("facts", [])
        meta = data.get("metadata", {})
        self.current_index = int(meta.get("current_index", 0))
        logger.info(
            "MathCafe loaded: %d facts, next index: %d",
            len(self.facts),
            self.current_index,
        )

    def save_facts(self) -> None:
        data = {
            "metadata": {
                "total_facts": len(self.facts),
                "last_updated": datetime.date.today().isoformat(),
                "current_index": self.current_index,
            },
            "facts": self.facts,
        }
        self._facts_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._facts_file, "w", encoding="utf-8") as fh:
            yaml.dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # ── Fact selection ────────────────────────────────────────────────────────

    def get_next_fact(self) -> Optional[dict]:
        if not self.facts:
            return None

        # Try current_index first; scan forward for next unposted
        n = len(self.facts)
        for offset in range(n):
            idx = (self.current_index + offset) % n
            if not self.facts[idx].get("posted", False):
                self.current_index = idx
                return self.facts[idx]

        # All facts have been posted — reset cycle
        logger.info("All MathCafe facts cycled, restarting from beginning")
        for fact in self.facts:
            fact["posted"] = False
            fact["posted_date"] = None
        self.current_index = 0
        return self.facts[0]

    # ── Discord helpers ───────────────────────────────────────────────────────

    def get_image_file(self, fact: dict) -> Optional[discord.File]:
        if not fact.get("needs_image"):
            return None
        filename = fact.get("image_filename")
        if not filename:
            return None
        image_path = self._images_dir / filename
        if image_path.exists():
            return discord.File(str(image_path))
        logger.warning("MathCafe image not found: %s. Place image at %s", filename, image_path)
        return None

    def build_embed(self, fact: dict, post_date: datetime.date) -> discord.Embed:
        embed = discord.Embed(title="☕ GSA MathCafe", color=MATHCAFE_COLOR)
        day_name = post_date.strftime("%A, %B %d")
        icon = "🧩" if fact.get("discussion") else "💡"

        embed.add_field(
            name=f"{icon} {fact['title']}",
            value=fact["body"],
            inline=False,
        )
        embed.set_footer(text=f"{fact.get('footer', 'GSA MathCafe')} · {day_name}")

        if fact.get("has_spoiler") and fact.get("spoiler_text"):
            embed.add_field(
                name="💡 Answer (spoiler)",
                value=f"||{fact['spoiler_text']}||",
                inline=False,
            )

        if fact.get("discussion"):
            embed.add_field(
                name="💬 Join the discussion",
                value="Reply with your answer! React below to vote.",
                inline=False,
            )
        return embed

    # ── Post ─────────────────────────────────────────────────────────────────

    async def post_fact(self, channel: discord.TextChannel) -> bool:
        fact = self.get_next_fact()
        if fact is None:
            logger.warning("MathCafe: no facts available to post")
            return False

        embed = self.build_embed(fact, datetime.date.today())
        image_file = self.get_image_file(fact)

        if image_file:
            message = await channel.send(file=image_file, embed=embed)
        else:
            message = await channel.send(embed=embed)

        # Add reactions
        for reaction in fact.get("reactions", []):
            await message.add_reaction(reaction)
            await asyncio.sleep(0.5)

        # Create discussion thread
        if fact.get("discussion"):
            try:
                thread = await message.create_thread(
                    name=f"Discussion: {fact['title'][:50]}",
                    auto_archive_duration=1440,
                )
                await thread.send(
                    "Share your answer here! 🧵\n"
                    "No spoilers in the main channel please."
                )
            except (discord.Forbidden, discord.HTTPException) as exc:
                logger.warning("MathCafe: could not create discussion thread: %s", exc)

        # Mark as posted
        fact["posted"] = True
        fact["posted_date"] = datetime.date.today().isoformat()
        self.current_index += 1
        self.save_facts()
        self.export_mathcafe_json()

        logger.info("MathCafe posted: '%s'", fact["title"])
        return True

    # ── Website export ────────────────────────────────────────────────────────

    def export_mathcafe_json(self, output_path: Optional[Path] = None) -> None:
        import json

        posted = [f for f in self.facts if f.get("posted")]
        posted_sorted = sorted(
            posted,
            key=lambda f: f.get("posted_date") or "",
            reverse=True,
        )
        recent = posted_sorted[:10]

        payload = {
            "last_updated": datetime.date.today().isoformat(),
            "total_facts": len(self.facts),
            "recent_facts": [
                {
                    "id": f["id"],
                    "title": f["title"],
                    "category": f["category"],
                    "body": f["body"],
                    "posted_date": f.get("posted_date"),
                    "discussion": f.get("discussion", False),
                    "needs_image": f.get("needs_image", False),
                    "image_filename": f.get("image_filename"),
                }
                for f in recent
            ],
        }

        out = output_path or Path("website/data/mathcafe.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        logger.info("MathCafe JSON exported to website")

    # ── Add fact programmatically ─────────────────────────────────────────────

    async def add_fact(
        self,
        title: str,
        body: str,
        category: str,
        discussion: bool = False,
        reactions: Optional[list[str]] = None,
        day_preference: str = "any",
        style: str = "short",
    ) -> dict:
        next_num = len(self.facts) + 1
        fact_id = f"mc_{next_num:03d}"

        new_fact: dict = {
            "id": fact_id,
            "title": title,
            "body": body,
            "category": category,
            "subcategory": "",
            "day_preference": day_preference,
            "style": style,
            "needs_image": False,
            "image_filename": None,
            "posted": False,
            "posted_date": None,
            "discussion": discussion,
            "reactions": reactions or [],
            "reaction_labels": {},
            "footer": "GSA MathCafe · Powered by GSA Gateway",
        }

        self.facts.append(new_fact)
        self.save_facts()
        logger.info("New MathCafe fact added: '%s'", title)
        return new_fact
