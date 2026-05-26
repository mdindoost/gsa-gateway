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
class PolicyEntry:
    title: str
    content: str
    source_file: str
    section: str


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


_POLICY_DOCS: list[tuple[str, str]] = [
    ("gsa_constitution.md", "gsa_constitution"),
    ("travel_award.md", "travel_award"),
    ("club_finance.md", "club_finance"),
]


@dataclass
class KnowledgeBase:
    """Container for all static knowledge loaded from data files."""

    data_dir: Path
    faq_entries: list[FAQEntry] = field(default_factory=list)
    policy_entries: list[PolicyEntry] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    contacts: dict[str, Contact] = field(default_factory=dict)
    resources: dict[str, list[Resource]] = field(default_factory=dict)

    def load(self) -> None:
        """Load (or reload) all data files."""
        self.faq_entries.clear()
        self.policy_entries.clear()
        self.events.clear()
        self.contacts.clear()
        self.resources.clear()

        self._load_faq()
        for filename, section in _POLICY_DOCS:
            self._load_policy_doc(filename, section)
        self._load_events()
        self._load_contacts()
        self._load_resources()

        policy_counts = {filename: 0 for filename, _ in _POLICY_DOCS}
        for entry in self.policy_entries:
            if entry.source_file in policy_counts:
                policy_counts[entry.source_file] += 1

        resource_count = sum(len(v) for v in self.resources.values())
        total = (
            len(self.faq_entries)
            + len(self.policy_entries)
            + len(self.contacts)
            + len(self.events)
            + resource_count
        )

        lines = ["Knowledge base files loaded:"]
        lines.append(f"  - gsa_faq.md: {len(self.faq_entries)} entries")
        for filename, _ in _POLICY_DOCS:
            lines.append(f"  - {filename}: {policy_counts.get(filename, 0)} entries")
        lines.append(f"  - contacts.yml: {len(self.contacts)} entries")
        lines.append(f"  - events.yml: {len(self.events)} entries")
        lines.append(f"  - resources.yml: {resource_count} entries")
        lines.append(f"  Total: {total} indexed items")
        print("\n".join(lines), flush=True)
        logger.info("\n".join(lines))

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

    def _load_policy_doc(self, filename: str, section_name: str) -> None:
        path = self.data_dir / filename
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read policy file %s: %s", filename, exc)
            return

        pattern = re.compile(r"^## (.+?)\n(.*?)(?=^## |\Z)", re.DOTALL | re.MULTILINE)
        count = 0
        for m in pattern.finditer(content):
            title = m.group(1).strip()
            body = m.group(2).strip()
            if body:
                self.policy_entries.append(
                    PolicyEntry(
                        title=title,
                        content=body,
                        source_file=filename,
                        section=section_name,
                    )
                )
                count += 1
        logger.debug("Policy entries loaded from %s: %d", filename, count)

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
        """Return a flat list of all KB items for fuzzy search.

        Indexes FAQ entries, contacts, events, and resources so every
        piece of data in the knowledge base is reachable via /ask.
        """
        items: list[dict[str, str]] = []

        # ── FAQ entries ───────────────────────────────────────────────────────
        for idx, entry in enumerate(self.faq_entries):
            items.append(
                {
                    "id": f"faq_{idx}",
                    "text": entry.question,
                    "content": entry.answer,
                    "type": "faq",
                    "section": "faq",
                    "source_file": "gsa_faq.md",
                }
            )

        # ── Policy document entries ───────────────────────────────────────────
        # Use title + first 300 chars of content as the search text so that
        # natural-language queries match the document body, not just the heading.
        for idx, entry in enumerate(self.policy_entries):
            search_text = f"{entry.title} {entry.content[:300]}"
            items.append(
                {
                    "id": f"policy_{idx}",
                    "text": search_text,
                    "content": entry.content,
                    "type": "policy",
                    "section": entry.section,
                    "source_file": entry.source_file,
                }
            )

        # ── Contacts — one combined "all officers" entry + one per person ─────
        officer_lines: list[str] = []
        for key, contact in self.contacts.items():
            # Per-contact entry
            parts = [f"The {contact.role} is {contact.name}."]
            if contact.email and contact.email != "N/A":
                parts.append(f"Email: {contact.email}.")
            if contact.office and contact.office != "N/A":
                parts.append(f"Office: {contact.office}.")
            if contact.hours and contact.hours != "N/A":
                parts.append(f"Hours: {contact.hours}.")
            if contact.notes:
                parts.append(contact.notes)
            content = " ".join(parts)
            items.append(
                {
                    "id": f"contact_{key}",
                    "text": f"{contact.role} {contact.name} contact",
                    "content": content,
                    "type": "contact",
                    "section": "contacts",
                    "source_file": "contacts.yml",
                }
            )
            # Accumulate for the combined officers entry
            if any(
                kw in contact.role.lower()
                for kw in ("president", "vp", "vice president", "secretary")
            ):
                line = f"{contact.role}: {contact.name}"
                if contact.email and contact.email != "N/A":
                    line += f" ({contact.email})"
                officer_lines.append(line)

        if officer_lines:
            items.append(
                {
                    "id": "contact_all_officers",
                    "text": "GSA officers executive board members list",
                    "content": (
                        "The current GSA Executive Board officers are: "
                        + "; ".join(officer_lines)
                        + ". All officers are available at Campus Center 110A, "
                        "weekdays 11:00 AM – 5:00 PM. "
                        "General inquiries: gsa-pres@njit.edu."
                    ),
                    "type": "contact",
                    "section": "contacts",
                    "source_file": "contacts.yml",
                }
            )

        # ── Events ────────────────────────────────────────────────────────────
        for idx, event in enumerate(self.events):
            parts = [
                f"{event.name} takes place on {event.date}",
                f"at {event.time}" if event.time and event.time != "TBD" else "",
                f"at {event.location}." if event.location and event.location != "TBD" else ".",
                event.description,
                f"Organized by {event.organizer}." if event.organizer else "",
                f"RSVP: {event.rsvp_link}" if event.rsvp_link else "",
            ]
            content = " ".join(p for p in parts if p).strip()
            items.append(
                {
                    "id": f"event_{idx}",
                    "text": f"event {event.name} {event.category}",
                    "content": content,
                    "type": "event",
                    "section": "events",
                    "source_file": "events.yml",
                }
            )

        # ── Resources — group by category for richer context ──────────────────
        for cat, resources in self.resources.items():
            for idx, resource in enumerate(resources):
                content = resource.description
                if resource.url:
                    content += f" Link: {resource.url}"
                items.append(
                    {
                        "id": f"resource_{cat}_{idx}",
                        "text": f"{resource.title} {cat} resource",
                        "content": content,
                        "type": "resource",
                        "section": f"resources/{cat}",
                        "source_file": "resources.yml",
                    }
                )

        return items

    def get_upcoming_events(self) -> list[Event]:
        """Return events sorted by date (earliest first)."""
        try:
            return sorted(self.events, key=lambda e: e.date)
        except Exception:
            return list(self.events)
