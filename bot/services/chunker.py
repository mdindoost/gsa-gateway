"""Document chunker — splits all KB files into token-bounded chunks for vector retrieval."""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import tiktoken
import yaml

logger = logging.getLogger(__name__)

MAX_TOKENS = 350
OVERLAP_TOKENS = 50
ENCODING = "cl100k_base"


@dataclass
class DocumentChunk:
    chunk_id: str
    text: str
    source_file: str
    source_type: str
    section_title: str
    metadata: dict
    token_count: int


class DocumentChunker:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._enc = tiktoken.get_encoding(ENCODING)

    def count_tokens(self, text: str) -> int:
        return len(self._enc.encode(text))

    def split_text_by_tokens(
        self,
        text: str,
        max_tokens: int = MAX_TOKENS,
        overlap_tokens: int = OVERLAP_TOKENS,
    ) -> list[str]:
        # Split into sentences, preserving newlines as split points too
        raw_sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
        sentences: list[str] = []
        for s in raw_sentences:
            for line in s.split("\n"):
                stripped = line.strip()
                if stripped:
                    sentences.append(stripped)

        chunks: list[str] = []
        current_parts: list[str] = []
        current_tokens = 0

        for sentence in sentences:
            s_tokens = self.count_tokens(sentence)
            if s_tokens > max_tokens:
                # Single sentence exceeds limit — hard split at token boundary
                if current_parts:
                    chunks.append(" ".join(current_parts))
                    current_parts = []
                    current_tokens = 0
                tokens = self._enc.encode(sentence)
                for start in range(0, len(tokens), max_tokens - overlap_tokens):
                    chunk_tokens = tokens[start : start + max_tokens]
                    chunks.append(self._enc.decode(chunk_tokens))
                # start overlap from the tail of the last sentence
                overlap_tokens_list = self._enc.encode(sentence)[-overlap_tokens:]
                current_parts = [self._enc.decode(overlap_tokens_list)]
                current_tokens = len(overlap_tokens_list)
                continue

            if current_tokens + s_tokens > max_tokens and current_parts:
                chunks.append(" ".join(current_parts))
                # carry overlap from end of saved chunk
                overlap_text = " ".join(current_parts)
                overlap_encoded = self._enc.encode(overlap_text)[-overlap_tokens:]
                overlap_str = self._enc.decode(overlap_encoded).strip()
                current_parts = [overlap_str] if overlap_str else []
                current_tokens = self.count_tokens(overlap_str) if overlap_str else 0

            current_parts.append(sentence)
            current_tokens += s_tokens

        if current_parts:
            chunks.append(" ".join(current_parts))

        return [c for c in chunks if c.strip()]

    def chunk_markdown_faq(self, filepath: Path) -> list[DocumentChunk]:
        content = filepath.read_text(encoding="utf-8")
        # Split on level-2 headings
        sections = re.split(r'^## ', content, flags=re.MULTILINE)
        chunks: list[DocumentChunk] = []
        section_idx = 0
        for section in sections:
            if not section.strip():
                continue
            # Extract section title (first line)
            lines = section.strip().split("\n", 1)
            section_title = lines[0].strip()
            body = lines[1].strip() if len(lines) > 1 else ""

            # Find Q&A pairs: **A:** format used in this FAQ
            qa_pairs = re.findall(
                r'Q:\s*(.+?)\n\*\*A:\*\*\s*(.+?)(?=\n##|\Z)',
                section,
                flags=re.DOTALL,
            )
            if not qa_pairs:
                # Also try inline format without ** around Q
                qa_pairs = re.findall(
                    r'Q:\s*(.+?)\n\*\*A:\*\*\s*(.+?)(?=\nQ:|\n##|\Z)',
                    body,
                    flags=re.DOTALL,
                )

            if not qa_pairs:
                section_idx += 1
                continue

            for qa_idx, (question, answer) in enumerate(qa_pairs):
                question = question.strip()
                answer = answer.strip()
                text = f"Question: {question}\nAnswer: {answer}"
                file_prefix = filepath.stem
                if self.count_tokens(text) <= MAX_TOKENS:
                    chunks.append(DocumentChunk(
                        chunk_id=f"{file_prefix}_faq_{section_idx}_{qa_idx}_0",
                        text=text,
                        source_file=filepath.name,
                        source_type="faq",
                        section_title=section_title,
                        metadata={},
                        token_count=self.count_tokens(text),
                    ))
                else:
                    answer_parts = self.split_text_by_tokens(answer)
                    for chunk_idx, part in enumerate(answer_parts):
                        prefix = (
                            f"Question: {question}\nAnswer: "
                            if chunk_idx == 0
                            else f"Question: {question}\nAnswer (continued): "
                        )
                        chunk_text = prefix + part
                        chunks.append(DocumentChunk(
                            chunk_id=f"{file_prefix}_faq_{section_idx}_{qa_idx}_{chunk_idx}",
                            text=chunk_text,
                            source_file=filepath.name,
                            source_type="faq",
                            section_title=section_title,
                            metadata={},
                            token_count=self.count_tokens(chunk_text),
                        ))
            section_idx += 1
        return chunks

    def chunk_markdown_policy(self, filepath: Path) -> list[DocumentChunk]:
        content = filepath.read_text(encoding="utf-8")
        # Extract document title from first # heading
        title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        doc_title = title_match.group(1).strip() if title_match else filepath.stem

        # Split on ## headings
        sections = re.split(r'^## ', content, flags=re.MULTILINE)
        chunks: list[DocumentChunk] = []
        for section_idx, section in enumerate(sections):
            if not section.strip():
                continue
            lines = section.strip().split("\n", 1)
            section_title = lines[0].strip()
            body = lines[1].strip() if len(lines) > 1 else ""

            # Normalize body: strip excessive whitespace, normalize bullet points
            body = re.sub(r'\n{3,}', '\n\n', body)
            body = re.sub(r'^\s*[\*\-•]\s+', '- ', body, flags=re.MULTILINE)

            full_text = f"{section_title}\n\n{body}"
            if self.count_tokens(full_text) <= MAX_TOKENS:
                chunks.append(DocumentChunk(
                    chunk_id=f"{filepath.stem}_{section_idx}_0",
                    text=full_text,
                    source_file=filepath.name,
                    source_type="policy",
                    section_title=section_title,
                    metadata={"document_title": doc_title},
                    token_count=self.count_tokens(full_text),
                ))
            else:
                sub_chunks = self.split_text_by_tokens(body)
                for chunk_idx, part in enumerate(sub_chunks):
                    chunk_text = f"{section_title}\n\n{part}"
                    chunks.append(DocumentChunk(
                        chunk_id=f"{filepath.stem}_{section_idx}_{chunk_idx}",
                        text=chunk_text,
                        source_file=filepath.name,
                        source_type="policy",
                        section_title=section_title,
                        metadata={"document_title": doc_title},
                        token_count=self.count_tokens(chunk_text),
                    ))
        return chunks

    def chunk_yaml_events(self, filepath: Path) -> list[DocumentChunk]:
        data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
        events = data.get("events", [])
        chunks: list[DocumentChunk] = []
        for idx, event in enumerate(events):
            name = event.get("name", "")
            date = event.get("date", "")
            time = event.get("time", "TBD")
            location = event.get("location", "TBD")
            description = event.get("description", "")
            organizer = event.get("organizer", "GSA")
            tags = event.get("tags", event.get("category", ""))
            if isinstance(tags, str):
                tags = [tags]
            rsvp_link = event.get("rsvp_link", "")

            text = (
                f"Event: {name}\n"
                f"Date: {date}\n"
                f"Time: {time}\n"
                f"Location: {location}\n"
                f"Description: {description}\n"
                f"Organizer: {organizer}\n"
                f"Tags: {', '.join(str(t) for t in tags)}\n"
                f"RSVP: {rsvp_link or 'See GSA channels for details'}"
            )
            chunks.append(DocumentChunk(
                chunk_id=f"event_{idx}",
                text=text,
                source_file=filepath.name,
                source_type="event",
                section_title=name,
                metadata={
                    "date": str(date),
                    "tags": ", ".join(str(t) for t in tags),
                    "location": location,
                },
                token_count=self.count_tokens(text),
            ))
        return chunks

    def chunk_yaml_contacts(self, filepath: Path) -> list[DocumentChunk]:
        data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
        contacts = data.get("contacts", {})
        chunks: list[DocumentChunk] = []
        officer_idx = 0
        office_idx = 0

        for key, contact in contacts.items():
            if isinstance(contact, dict):
                # Distinguish officers from campus offices by presence of "role"
                if "role" in contact or "email" in contact:
                    # Officer entry
                    role = contact.get("role", key)
                    name = contact.get("name", "")
                    email = contact.get("email", "")
                    program = contact.get("program", contact.get("notes", ""))
                    office = contact.get("office", "")
                    hours = contact.get("hours", "")
                    responsibilities = contact.get("responsibilities", contact.get("notes", ""))

                    text = (
                        f"GSA Officer — {role}\n"
                        f"Name: {name}\n"
                        f"Email: {email}\n"
                        f"Program: {program}\n"
                        f"Office: {office}\n"
                        f"Hours: {hours}\n"
                        f"Responsibilities: {responsibilities}"
                    )
                    chunks.append(DocumentChunk(
                        chunk_id=f"contact_officer_{officer_idx}",
                        text=text,
                        source_file=filepath.name,
                        source_type="contact",
                        section_title=f"GSA Officer: {role}",
                        metadata={"role": role, "email": email, "name": name},
                        token_count=self.count_tokens(text),
                    ))
                    officer_idx += 1

        # Handle campus_offices list if present
        campus_offices = data.get("campus_offices", [])
        for office in campus_offices:
            name = office.get("name", "")
            description = office.get("description", "")
            email = office.get("email", "")
            location = office.get("location", "")
            hours = office.get("hours", "")
            website = office.get("website", "")

            text = (
                f"Campus Office — {name}\n"
                f"Description: {description}\n"
                f"Email: {email or 'See website'}\n"
                f"Location: {location or 'See website'}\n"
                f"Hours: {hours or 'See website'}\n"
                f"Website: {website or 'N/A'}"
            )
            chunks.append(DocumentChunk(
                chunk_id=f"contact_office_{office_idx}",
                text=text,
                source_file=filepath.name,
                source_type="contact",
                section_title=f"Campus Office: {name}",
                metadata={"name": name, "email": email, "role": "campus_office"},
                token_count=self.count_tokens(text),
            ))
            office_idx += 1

        return chunks

    def chunk_yaml_resources(self, filepath: Path) -> list[DocumentChunk]:
        data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
        resources = data.get("resources", {})
        chunks: list[DocumentChunk] = []

        for category, items in resources.items():
            if not isinstance(items, list):
                continue
            for idx, item in enumerate(items):
                name = item.get("name", "")
                description = item.get("description", "")
                link = item.get("link", item.get("url", ""))
                contact = item.get("contact", item.get("email", ""))

                text = (
                    f"Resource: {name}\n"
                    f"Category: {category}\n"
                    f"Description: {description}\n"
                    f"Link: {link or 'Contact GSA office'}\n"
                    f"Contact: {contact or 'See GSA office'}"
                )
                chunks.append(DocumentChunk(
                    chunk_id=f"resource_{category}_{idx}",
                    text=text,
                    source_file=filepath.name,
                    source_type="resource",
                    section_title=f"Resource: {name}",
                    metadata={"category": category, "link": link},
                    token_count=self.count_tokens(text),
                ))
        return chunks

    def chunk_all(self) -> list[DocumentChunk]:
        all_chunks: list[DocumentChunk] = []
        file_counts: dict[str, int] = {}

        tasks: list[tuple[str, callable]] = [
            ("gsa_faq.md",          self.chunk_markdown_faq),
            ("gsa_constitution.md", self.chunk_markdown_policy),
            ("travel_award.md",     self.chunk_markdown_policy),
            ("club_finance.md",     self.chunk_markdown_policy),
            ("rules.md",            self.chunk_markdown_policy),
            ("mmi_workshop.md",     self.chunk_markdown_faq),
            ("bot_features.md",     self.chunk_markdown_faq),
            ("events.yml",          self.chunk_yaml_events),
            ("contacts.yml",        self.chunk_yaml_contacts),
            ("resources.yml",       self.chunk_yaml_resources),
        ]

        for filename, method in tasks:
            filepath = self.data_dir / filename
            try:
                chunks = method(filepath)
                all_chunks.extend(chunks)
                file_counts[filename] = len(chunks)
            except FileNotFoundError:
                logger.warning("Chunker: file not found — skipping %s", filepath)
                file_counts[filename] = 0
            except Exception as exc:
                logger.warning("Chunker: error processing %s: %s", filename, exc)
                file_counts[filename] = 0

        logger.info(
            "Chunker complete: %d chunks from %d files",
            len(all_chunks),
            sum(1 for c in file_counts.values() if c > 0),
        )
        for fname, count in file_counts.items():
            logger.info("  - %s: %d chunks", fname, count)

        return all_chunks
