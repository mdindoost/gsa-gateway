"""Generate human-readable text summaries for admin use."""

import logging
from datetime import datetime
from typing import Any

from bot.services.database import Database

logger = logging.getLogger(__name__)


class SummaryService:
    """Generates weekly summary reports from the database."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def weekly_summary(self, days: int = 7) -> str:
        """Return a markdown-formatted summary of the last *days* days."""
        initiatives = self.db.get_recent_initiatives(days)
        feedback = self.db.get_recent_feedback(days)
        stats = self.db.get_stats()
        generated = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        lines: list[str] = [
            f"**GSA Gateway — {days}-Day Summary**",
            f"*Generated: {generated}*",
            "",
            "**All-Time Engagement**",
            f"• Questions asked: {stats['total_questions']}",
            f"• Initiatives submitted: {stats['total_initiatives']}",
            f"• Feedback items: {stats['total_feedback']}",
            "",
        ]

        if stats["top_topics"]:
            lines.append("**Top Search Topics (all time)**")
            for t in stats["top_topics"]:
                lines.append(f"• {t['matched_topic']} — {t['count']} queries")
            lines.append("")

        if initiatives:
            lines.append(f"**New Initiatives (last {days} days) — {len(initiatives)}**")
            for i in initiatives:
                contact_flag = "wants contact" if i["include_contact"] else "anonymous"
                lines.append(
                    f"• [{i['category'].upper()}] **{i['title']}** ({contact_flag})"
                )
                desc_preview = i["description"][:120].replace("\n", " ")
                lines.append(f"  _{desc_preview}…_")
            lines.append("")
        else:
            lines += [f"**New Initiatives (last {days} days):** None", ""]

        if feedback:
            lines.append(f"**Recent Feedback (last {days} days) — {len(feedback)}**")
            for fb in feedback[:8]:
                preview = fb["message"][:120].replace("\n", " ")
                lines.append(f"• {preview}")
            if len(feedback) > 8:
                lines.append(f"  *…and {len(feedback) - 8} more*")
        else:
            lines.append(f"**Recent Feedback (last {days} days):** None")

        return "\n".join(lines)
