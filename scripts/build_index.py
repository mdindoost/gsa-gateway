#!/usr/bin/env python3
"""
GSA Gateway — Vector Index Builder
Run this script once to build the ChromaDB knowledge base index.
Usage: python scripts/build_index.py [--reset]
"""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.config import config
from bot.services.chunker import DocumentChunker
from bot.services.embedder import EmbeddingService
from bot.services.vector_store import VectorStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


async def main(reset: bool = False) -> None:
    print("=" * 60)
    print("GSA Gateway — Vector Index Builder")
    print("=" * 60)

    data_dir = Path("bot/data")

    print("\n[1/5] Initializing services...")
    embedder = EmbeddingService(
        base_url=config.ollama_url,
        model=config.embedding_model,
    )
    vector_store = VectorStore(db_path=config.chroma_db_path)

    print("[2/5] Checking Ollama connection...")
    connected = await embedder.check_connection()
    if not connected:
        print("ERROR: Cannot connect to Ollama.")
        print("Make sure Ollama is running: ollama serve")
        print("Make sure model is available: ollama pull nomic-embed-text")
        await embedder.close()
        sys.exit(1)
    print("✓ Ollama connected")

    if reset:
        print("[3/5] Resetting vector store...")
        vector_store.reset()
        print("✓ Vector store reset")
    elif not vector_store.is_empty():
        print(
            f"[3/5] Vector store already has "
            f"{vector_store.get_chunk_count()} chunks."
        )
        answer = input("Reset and rebuild? (y/N): ").strip().lower()
        if answer == "y":
            vector_store.reset()
            print("✓ Vector store reset")
        else:
            print("Aborted. Use --reset flag to force rebuild.")
            await embedder.close()
            sys.exit(0)
    else:
        print("[3/5] Vector store is empty — will build fresh.")

    print("\n[4/5] Chunking knowledge base documents...")
    start = time.time()
    chunker = DocumentChunker(data_dir=data_dir)
    chunks = chunker.chunk_all()
    elapsed = time.time() - start
    print(f"✓ Created {len(chunks)} chunks in {elapsed:.1f}s")

    print(f"\n[5/5] Embedding {len(chunks)} chunks...")
    print("      This may take 2-5 minutes depending on hardware.")
    print("      Progress will be shown every 10 chunks.\n")

    start = time.time()
    texts = [chunk.text for chunk in chunks]
    embeddings = await embedder.embed_batch(texts, batch_size=10)
    elapsed = time.time() - start

    success_count = sum(1 for e in embeddings if e is not None)
    fail_count = len(embeddings) - success_count

    print(f"\n✓ Embedded {success_count}/{len(chunks)} chunks in {elapsed:.1f}s")
    if fail_count > 0:
        print(f"⚠ {fail_count} chunks failed to embed (will be skipped)")

    vector_store.add_chunks(chunks, embeddings)

    stats = vector_store.get_stats()
    print("\n" + "=" * 60)
    print("INDEX BUILD COMPLETE")
    print("=" * 60)
    print(f"Total chunks indexed: {stats['total_chunks']}")
    print("\nBy source type:")
    for type_name, count in stats["by_source_type"].items():
        print(f"  {type_name}: {count} chunks")
    print("\nBy source file:")
    for file_name, count in stats["by_source_file"].items():
        print(f"  {file_name}: {count} chunks")
    print("\nThe bot is ready to start.")
    print("Run: sudo systemctl restart gsa-gateway")
    print("=" * 60)

    await embedder.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset vector store before building",
    )
    args = parser.parse_args()
    asyncio.run(main(reset=args.reset))
