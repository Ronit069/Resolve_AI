"""
Tests for the expanded RAG knowledge base content
(scripts/data/knowledge/*.md, ingested by scripts/ingest_razorpay_policies.py).

Replaces a single 55-line, reason_code-less document with one document
per dispute category, several tagged with a real reason_code -- these
tests guard the manifest/content structure, not retrieval itself
(already covered by tests/test_module_g_step5_g04.py), so a future
content edit can't silently break ingestion or produce a document with
no real chunking structure.
"""

import os

from app.services.rag.chunking import MarkdownChunker
from scripts.ingest_razorpay_policies import DOCUMENTS

KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "data", "knowledge")


def test_every_manifest_entry_has_a_file_on_disk():
    for doc in DOCUMENTS:
        file_path = os.path.join(KNOWLEDGE_DIR, doc["file"])
        assert os.path.isfile(file_path), f"Missing knowledge file: {doc['file']}"


def test_every_document_has_substantial_content_and_a_title():
    for doc in DOCUMENTS:
        with open(os.path.join(KNOWLEDGE_DIR, doc["file"]), encoding="utf-8") as f:
            content = f.read()
        assert len(content) > 500, f"{doc['file']} looks too thin to be useful retrieval content"
        assert content.lstrip().startswith("# "), f"{doc['file']} must start with a top-level heading"


def test_every_document_chunks_into_multiple_sections():
    """Each document must have real ## subsections, not be one giant blob --
    otherwise retrieval can only ever return the whole document at once."""
    for doc in DOCUMENTS:
        with open(os.path.join(KNOWLEDGE_DIR, doc["file"]), encoding="utf-8") as f:
            content = f.read()
        chunks = MarkdownChunker.chunk_content(content, source_metadata={})
        assert len(chunks) >= 3, f"{doc['file']} only produced {len(chunks)} chunk(s)"


def test_reason_code_tagged_documents_are_distinct():
    """Each explicitly reason_code-tagged document must cover a distinct
    code -- otherwise ingest_razorpay_policies.py's idempotent-by-identity
    convention (merchant_id, source_type, reason_code) would treat two
    categories as versions of the same source and deprecate one."""
    reason_codes = [doc["reason_code"] for doc in DOCUMENTS if doc["reason_code"] is not None]
    assert len(reason_codes) == len(set(reason_codes))
    assert len(reason_codes) >= 3, "Expected several distinct reason-code-tagged categories"


def test_fraud_document_matches_the_reason_code_used_elsewhere_in_the_repo():
    """reason_code="10.4" is the one value the rest of the system (synthetic
    training data, demo seeding, every fixture) actually exercises -- the
    fraud category must be tagged with exactly that code, not a different
    one, or the exact-match retrieval path would never fire for it."""
    fraud_docs = [doc for doc in DOCUMENTS if doc["file"] == "fraud_unauthorized_transaction.md"]
    assert len(fraud_docs) == 1
    assert fraud_docs[0]["reason_code"] == "10.4"
