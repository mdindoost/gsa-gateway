"""ChromaDB vector store — stores knowledge base chunks and their embeddings."""

import logging
from typing import Optional

import chromadb
import chromadb.config

from bot.services.chunker import DocumentChunk

logger = logging.getLogger(__name__)

COLLECTION_NAME = "gsa_knowledge_base"
COLLECTION_VERSION = "v1"


class VectorStore:
    def __init__(self, db_path: str) -> None:
        self.client = chromadb.PersistentClient(
            path=db_path,
            settings=chromadb.config.Settings(
                anonymized_telemetry=False,
                allow_reset=True,
            ),
        )
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={
                "hnsw:space": "cosine",
                "version": COLLECTION_VERSION,
            },
        )
        logger.info("VectorStore initialized at %s", db_path)
        logger.info(
            "Collection '%s' has %d documents",
            COLLECTION_NAME,
            self.collection.count(),
        )

    def is_empty(self) -> bool:
        return self.collection.count() == 0

    def add_chunks(
        self,
        chunks: list[DocumentChunk],
        embeddings: list[Optional[list[float]]],
    ) -> None:
        valid_chunks = []
        valid_embeddings = []
        skipped = 0

        for chunk, emb in zip(chunks, embeddings):
            if emb is None:
                logger.warning("Skipping chunk %s — no embedding", chunk.chunk_id)
                skipped += 1
                continue
            valid_chunks.append(chunk)
            valid_embeddings.append(emb)

        if not valid_chunks:
            logger.warning("No valid chunks to add to vector store")
            return

        ids = [c.chunk_id for c in valid_chunks]
        documents = [c.text for c in valid_chunks]
        metadatas = [
            {
                "source_file": c.source_file,
                "source_type": c.source_type,
                "section_title": c.section_title,
                "token_count": str(c.token_count),
                **{k: str(v) for k, v in c.metadata.items()},
            }
            for c in valid_chunks
        ]

        self.collection.add(
            ids=ids,
            documents=documents,
            embeddings=valid_embeddings,
            metadatas=metadatas,
        )
        logger.info(
            "Added %d chunks to vector store (%d skipped due to embedding errors)",
            len(valid_chunks),
            skipped,
        )

    def add_single_chunk(
        self,
        chunk: DocumentChunk,
        embedding: list[float],
    ) -> None:
        metadata = {
            "source_file": chunk.source_file,
            "source_type": chunk.source_type,
            "section_title": chunk.section_title,
            "token_count": str(chunk.token_count),
            **{k: str(v) for k, v in chunk.metadata.items()},
        }
        try:
            existing = self.collection.get(ids=[chunk.chunk_id])
            if existing["ids"]:
                self.collection.update(
                    ids=[chunk.chunk_id],
                    documents=[chunk.text],
                    embeddings=[embedding],
                    metadatas=[metadata],
                )
            else:
                self.collection.add(
                    ids=[chunk.chunk_id],
                    documents=[chunk.text],
                    embeddings=[embedding],
                    metadatas=[metadata],
                )
        except Exception:
            self.collection.add(
                ids=[chunk.chunk_id],
                documents=[chunk.text],
                embeddings=[embedding],
                metadatas=[metadata],
            )
        logger.debug("Added/updated chunk: %s", chunk.chunk_id)

    def query(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        source_type_filter: Optional[str] = None,
        source_file_filter: Optional[str] = None,
    ) -> list[dict]:
        try:
            count = self.collection.count()
            if count == 0:
                return []

            n = min(n_results, count)

            where: Optional[dict] = None
            if source_type_filter and source_file_filter:
                where = {"$and": [
                    {"source_type": source_type_filter},
                    {"source_file": source_file_filter},
                ]}
            elif source_type_filter:
                where = {"source_type": source_type_filter}
            elif source_file_filter:
                where = {"source_file": source_file_filter}

            kwargs: dict = dict(
                query_embeddings=[query_embedding],
                n_results=n,
                include=["documents", "metadatas", "distances"],
            )
            if where:
                kwargs["where"] = where

            results = self.collection.query(**kwargs)

            parsed: list[dict] = []
            ids = results.get("ids", [[]])[0]
            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for chunk_id, text, meta, distance in zip(ids, documents, metadatas, distances):
                parsed.append({
                    "chunk_id": chunk_id,
                    "text": text,
                    "source_file": meta.get("source_file", ""),
                    "source_type": meta.get("source_type", ""),
                    "section_title": meta.get("section_title", ""),
                    "similarity": 1.0 - distance,
                    "metadata": meta,
                })

            parsed.sort(key=lambda x: x["similarity"], reverse=True)
            return parsed

        except Exception as exc:
            logger.error("VectorStore query error: %s", exc, exc_info=True)
            return []

    def get_chunk_count(self) -> int:
        return self.collection.count()

    def get_stats(self) -> dict:
        count = self.collection.count()
        if count == 0:
            return {
                "total_chunks": 0,
                "by_source_type": {},
                "by_source_file": {},
            }

        by_source_type: dict[str, int] = {}
        by_source_file: dict[str, int] = {}

        for source_type in ("faq", "policy", "event", "contact", "resource"):
            try:
                results = self.collection.get(
                    where={"source_type": source_type},
                    include=[],
                )
                by_source_type[source_type] = len(results.get("ids", []))
            except Exception:
                by_source_type[source_type] = 0

        for source_file in (
            "gsa_faq.md", "gsa_constitution.md", "travel_award.md",
            "club_finance.md", "rules.md", "events.yml",
            "contacts.yml", "resources.yml",
        ):
            try:
                results = self.collection.get(
                    where={"source_file": source_file},
                    include=[],
                )
                by_source_file[source_file] = len(results.get("ids", []))
            except Exception:
                by_source_file[source_file] = 0

        return {
            "total_chunks": count,
            "by_source_type": by_source_type,
            "by_source_file": by_source_file,
        }

    def reset(self) -> None:
        logger.warning("Vector store reset — all chunks deleted")
        self.client.delete_collection(COLLECTION_NAME)
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={
                "hnsw:space": "cosine",
                "version": COLLECTION_VERSION,
            },
        )
