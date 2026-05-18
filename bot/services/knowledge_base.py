"""Load and expose all knowledge base data files (MD + YAML)."""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class FAQEntry:
    question: str
    answer: str
    section: str = "faq"


@dataclass
class Event:
    name: str
    date: str
    time: str
    location: str
    description: str
    organizer: str
    rsvp_link: str
    category: str = "general"


@dataclass
class Contact:
    role: str
    name: str = "TBD"
    email: str = "N/A"
    office: str = "N/A"
    hours: str = "N/A"
    notes: str = ""


@dataclass
class Resource:
    title: str
    description: str
    url: str
    category: str


@dataclass
class KnowledgeBase:
    """Container for all static knowledge loaded from data files."""

    data_dir: Path
    faq_entries: list[FAQEntry] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    contacts: dict[str, Contact] = field(default_factory=dict)
    resources: dict[str, list[Resource]] = field(default_factory=dict)

    def load(self) -> None:
        """Load (or reload) all data files."""
        self.faq_entries.clear()
        self.events.clear()
        self.contacts.clear()
        self.resources.clear()

        self._load_faq()
        self._load_events()
        self._load_contacts()
        self._load_resources()

        logger.info(
            "Knowledge base loaded: %d FAQs, %d events, %d contacts, %d resource categories",
            len(self.faq_entries),
            len(self.events),
            len(self.contacts),
            len(self.resources),
        )

    # ── Loaders ───────────────────────────────────────────────────────────────

    def _load_faq(self) -> None:
        path = self.data_dir / "gsa_faq.md"
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("Cannot read FAQ file: %s", exc)
            return

        # ^ anchors to line-start (MULTILINE) so comments mentioning "## Q:" are skipped
        pattern = re.compile(
            r"^## Q: (.+?)\n\*\*A:\*\* (.*?)(?=^## Q:|\Z)", re.DOTALL | re.MULTILINE
        )
        for m in pattern.finditer(content):
            self.faq_entries.append(
                FAQEntry(
                    question=m.group(1).strip(),
                    answer=m.group(2).strip(),
                )
            )
        logger.debug("FAQ entries loaded: %d", len(self.faq_entries))

    def _load_events(self) -> None:
        path = self.data_dir / "events.yml"
        try:
            with open(path, encoding="utf-8") as fh:
                data: dict[str, Any] = yaml.safe_load(fh) or {}
        except OSError as exc:
            logger.error("Cannot read events file: %s", exc)
            return

        for ev in data.get("events", []):
            self.events.append(
                Event(
                    name=ev["name"],
                    date=str(ev["date"]),
                    time=ev.get("time", "TBD"),
                    location=ev.get("location", "TBD"),
                    description=ev.get("description", ""),
                    organizer=ev.get("organizer", "GSA"),
                    rsvp_link=ev.get("rsvp_link", ""),
                    category=ev.get("category", "general"),
                )
            )
        logger.debug("Events loaded: %d", len(self.events))

    def _load_contacts(self) -> None:
        path = self.data_dir / "contacts.yml"
        try:
            with open(path, encoding="utf-8") as fh:
                data: dict[str, Any] = yaml.safe_load(fh) or {}
        except OSError as exc:
            logger.error("Cannot read contacts file: %s", exc)
            return

        for key, info in data.get("contacts", {}).items():
            self.contacts[key] = Contact(
                role=info.get("role", key),
                name=info.get("name", "TBD"),
                email=info.get("email", "N/A"),
                office=info.get("office", "N/A"),
                hours=info.get("hours", "N/A"),
                notes=info.get("notes", ""),
            )
        logger.debug("Contacts loaded: %d", len(self.contacts))

    def _load_resources(self) -> None:
        path = self.data_dir / "resources.yml"
        try:
            with open(path, encoding="utf-8") as fh:
                data: dict[str, Any] = yaml.safe_load(fh) or {}
        except OSError as exc:
            logger.error("Cannot read resources file: %s", exc)
            return

        for cat, items in data.get("resources", {}).items():
            self.resources[cat] = [
                Resource(
                    title=item["title"],
                    description=item.get("description", ""),
                    url=item.get("url", ""),
                    category=cat,
                )
                for item in items
            ]
        logger.debug("Resource categories loaded: %d", len(self.resources))

    # ── Query helpers ─────────────────────────────────────────────────────────

    def get_searchable_texts(self) -> list[dict[str, str]]:
        """Return a flat list of items suitable for fuzzy search."""
        items = []
        for idx, entry in enumerate(self.faq_entries):
            items.append(
                {
                    "id": f"faq_{idx}",
                    "text": entry.question,
                    "content": entry.answer,
                    "type": "faq",
                    "section": entry.section,
                }
            )
        return items

    def get_upcoming_events(self) -> list[Event]:
        """Return events sorted by date (earliest first)."""
        try:
            return sorted(self.events, key=lambda e: e.date)
        except Exception:
            return list(self.events)
