import logging
import uuid
from pathlib import Path
import chromadb
from sentence_transformers import SentenceTransformer
from config import (
    CHROMA_PERSIST_DIR,
    EMBEDDING_MODEL,
    RAG_CHUNK_SIZE,
    RAG_CHUNK_OVERLAP,
    RAG_TOP_K,
)

logger = logging.getLogger(__name__)

DOCUMENTS_COLLECTION = "documents"
CONVERSATIONS_COLLECTION = "conversations"
PAGES_COLLECTION = "pages"


class RAGService:
    def __init__(self):
        self._embedder: SentenceTransformer | None = None
        self._chroma: chromadb.ClientAPI | None = None
        self._doc_collection = None
        self._conv_collection = None
        self._pages_collection = None

    def load(self):
        logger.info("Loading embedding model: %s", EMBEDDING_MODEL)
        self._embedder = SentenceTransformer(EMBEDDING_MODEL, device="cpu")
        logger.info("Embedding model loaded")

        logger.info("Initializing ChromaDB at %s", CHROMA_PERSIST_DIR)
        Path(CHROMA_PERSIST_DIR).mkdir(parents=True, exist_ok=True)
        self._chroma = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
        self._doc_collection = self._chroma.get_or_create_collection(
            name=DOCUMENTS_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        self._conv_collection = self._chroma.get_or_create_collection(
            name=CONVERSATIONS_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        self._pages_collection = self._chroma.get_or_create_collection(
            name=PAGES_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("ChromaDB initialized: docs=%d, convs=%d, pages=%d",
                     self._doc_collection.count(), self._conv_collection.count(),
                     self._pages_collection.count())

    @property
    def embedder(self) -> SentenceTransformer:
        if self._embedder is None:
            raise RuntimeError("RAG service not loaded. Call load() first.")
        return self._embedder

    @property
    def doc_collection(self):
        if self._doc_collection is None:
            raise RuntimeError("RAG service not loaded. Call load() first.")
        return self._doc_collection

    @property
    def conv_collection(self):
        if self._conv_collection is None:
            raise RuntimeError("RAG service not loaded. Call load() first.")
        return self._conv_collection

    @property
    def pages_collection(self):
        if self._pages_collection is None:
            raise RuntimeError("RAG service not loaded. Call load() first.")
        return self._pages_collection

    # --- Document ingestion ---

    def ingest_text(self, text: str, doc_id: str, filename: str, source_type: str = "text", session_id: str = "") -> int:
        """Chunk text and store embeddings. Returns number of chunks."""
        chunks = self._chunk_text(text, RAG_CHUNK_SIZE, RAG_CHUNK_OVERLAP)
        if not chunks:
            return 0

        embeddings = self.embedder.encode(chunks).tolist()
        ids = [f"{doc_id}_chunk_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "doc_id": doc_id,
                "filename": filename,
                "chunk_index": i,
                "page_number": -1,  # no page info for plain text
                "source_type": source_type,
                "session_id": session_id,
            }
            for i in range(len(chunks))
        ]

        self.doc_collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas,
        )
        logger.info("Ingested %d chunks for doc %s (%s)", len(chunks), doc_id, filename)
        return len(chunks)

    def ingest_pages(self, pages: list[tuple[int, str]], doc_id: str, filename: str, source_type: str = "pdf", session_id: str = "") -> int:
        """Ingest per-page text: store full pages and chunked embeddings with page metadata."""
        total_chunks = 0

        for page_num, page_text in pages:
            page_text = page_text.strip()
            if not page_text:
                continue

            # Store full page in pages collection
            page_id = f"{doc_id}_page_{page_num}"
            page_embedding = self.embedder.encode([page_text]).tolist()
            self.pages_collection.add(
                ids=[page_id],
                embeddings=page_embedding,
                documents=[page_text],
                metadatas=[{
                    "doc_id": doc_id,
                    "filename": filename,
                    "page_number": page_num,
                    "source_type": source_type,
                    "session_id": session_id,
                }],
            )

            # Chunk the page text and store in doc collection
            chunks = self._chunk_text(page_text, RAG_CHUNK_SIZE, RAG_CHUNK_OVERLAP)
            if not chunks:
                continue

            embeddings = self.embedder.encode(chunks).tolist()
            ids = [f"{doc_id}_page{page_num}_chunk_{i}" for i in range(len(chunks))]
            metadatas = [
                {
                    "doc_id": doc_id,
                    "filename": filename,
                    "chunk_index": total_chunks + i,
                    "page_number": page_num,
                    "source_type": source_type,
                    "session_id": session_id,
                }
                for i in range(len(chunks))
            ]

            self.doc_collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=chunks,
                metadatas=metadatas,
            )
            total_chunks += len(chunks)

        logger.info("Ingested %d pages (%d chunks) for doc %s (%s)",
                     len(pages), total_chunks, doc_id, filename)
        return total_chunks

    def ingest_file(self, file_path: str, filename: str, session_id: str = "") -> tuple[str, int]:
        """Parse and ingest a file. Returns (doc_id, chunk_count)."""
        doc_id = str(uuid.uuid4())
        ext = Path(filename).suffix.lower()

        if ext == ".pdf":
            pages = self._parse_pdf(file_path)
            chunk_count = self.ingest_pages(pages, doc_id, filename, source_type="pdf", session_id=session_id)
        elif ext in (".txt", ".md", ".text"):
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            chunk_count = self.ingest_text(text, doc_id, filename, source_type=ext.lstrip("."), session_id=session_id)
        else:
            raise ValueError(f"Unsupported file type: {ext}")

        return doc_id, chunk_count

    def ingest_bytes(self, data: bytes, filename: str, session_id: str = "") -> tuple[str, int]:
        """Parse and ingest file bytes. Returns (doc_id, chunk_count)."""
        doc_id = str(uuid.uuid4())
        ext = Path(filename).suffix.lower()

        if ext == ".pdf":
            pages = self._parse_pdf_bytes(data)
            chunk_count = self.ingest_pages(pages, doc_id, filename, source_type="pdf", session_id=session_id)
        elif ext in (".txt", ".md", ".text"):
            text = data.decode("utf-8", errors="replace")
            chunk_count = self.ingest_text(text, doc_id, filename, source_type=ext.lstrip("."), session_id=session_id)
        else:
            raise ValueError(f"Unsupported file type: {ext}")

        return doc_id, chunk_count

    # --- Retrieval ---

    def retrieve(self, query: str, k: int | None = None, session_id: str | None = None) -> list[dict]:
        """Retrieve top-k relevant document chunks with metadata, optionally filtered by session."""
        k = k or RAG_TOP_K
        if self.doc_collection.count() == 0:
            return []

        query_embedding = self.embedder.encode([query]).tolist()
        where_filter = {"session_id": session_id} if session_id else None
        try:
            results = self.doc_collection.query(
                query_embeddings=query_embedding,
                n_results=min(k, self.doc_collection.count()),
                include=["documents", "metadatas"],
                where=where_filter,
            )
        except Exception:
            # Fallback: if filter fails (e.g. no session_id metadata on old entries)
            results = self.doc_collection.query(
                query_embeddings=query_embedding,
                n_results=min(k, self.doc_collection.count()),
                include=["documents", "metadatas"],
            )

        if not results["documents"] or not results["documents"][0]:
            return []

        chunks = []
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            chunks.append({
                "text": doc,
                "doc_id": meta.get("doc_id", ""),
                "filename": meta.get("filename", ""),
                "page_number": meta.get("page_number", -1),
                "chunk_index": meta.get("chunk_index", 0),
                "source_type": meta.get("source_type", ""),
            })
        return chunks

    def retrieve_pages(self, query: str, k: int = 3, session_id: str | None = None) -> list[dict]:
        """Retrieve top-k relevant FULL PAGES (not chunks) with metadata."""
        if self.pages_collection.count() == 0:
            return []

        query_embedding = self.embedder.encode([query]).tolist()
        where_filter = {"session_id": session_id} if session_id else None
        try:
            results = self.pages_collection.query(
                query_embeddings=query_embedding,
                n_results=min(k, self.pages_collection.count()),
                include=["documents", "metadatas"],
                where=where_filter,
            )
        except Exception:
            results = self.pages_collection.query(
                query_embeddings=query_embedding,
                n_results=min(k, self.pages_collection.count()),
                include=["documents", "metadatas"],
            )

        if not results["documents"] or not results["documents"][0]:
            return []

        pages = []
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            pages.append({
                "text": doc,
                "doc_id": meta.get("doc_id", ""),
                "filename": meta.get("filename", ""),
                "page_number": meta.get("page_number", -1),
                "source_type": meta.get("source_type", ""),
            })
        return pages

    def retrieve_conversations(self, query: str, k: int = 2, session_id: str | None = None) -> list[str]:
        """Retrieve relevant past conversation exchanges."""
        if self.conv_collection.count() == 0:
            return []

        query_embedding = self.embedder.encode([query]).tolist()
        where_filter = {"session_id": session_id} if session_id else None
        try:
            results = self.conv_collection.query(
                query_embeddings=query_embedding,
                n_results=min(k, self.conv_collection.count()),
                where=where_filter,
            )
        except Exception:
            # If filter fails (e.g. no session_id metadata on old entries), query without filter
            results = self.conv_collection.query(
                query_embeddings=query_embedding,
                n_results=min(k, self.conv_collection.count()),
            )
        return results["documents"][0] if results["documents"] else []

    def store_conversation(self, user_text: str, assistant_text: str, session_id: str | None = None):
        """Store a conversation exchange for future retrieval."""
        exchange = f"User: {user_text}\nAssistant: {assistant_text}"
        exchange_id = str(uuid.uuid4())
        embedding = self.embedder.encode([exchange]).tolist()
        metadata = {"type": "conversation"}
        if session_id:
            metadata["session_id"] = session_id
        self.conv_collection.add(
            ids=[exchange_id],
            embeddings=embedding,
            documents=[exchange],
            metadatas=[metadata],
        )

    # --- Page retrieval ---

    def get_page(self, doc_id: str, page_number: int) -> dict | None:
        """Fetch a full page from the pages collection."""
        page_id = f"{doc_id}_page_{page_number}"
        try:
            result = self.pages_collection.get(
                ids=[page_id],
                include=["documents", "metadatas"],
            )
        except Exception:
            return None

        if not result["documents"]:
            return None

        meta = result["metadatas"][0] if result["metadatas"] else {}
        return {
            "text": result["documents"][0],
            "filename": meta.get("filename", ""),
            "page_number": page_number,
            "doc_id": doc_id,
        }

    # --- Document management ---

    def list_documents(self, session_id: str | None = None) -> list[dict]:
        """List ingested documents, optionally filtered by session."""
        if self.doc_collection.count() == 0:
            return []

        all_meta = self.doc_collection.get(include=["metadatas"])
        docs: dict[str, dict] = {}
        for meta in all_meta["metadatas"]:
            # Filter by session if requested
            if session_id is not None and meta.get("session_id", "") != session_id:
                continue
            did = meta["doc_id"]
            if did not in docs:
                docs[did] = {
                    "doc_id": did,
                    "filename": meta["filename"],
                    "source_type": meta.get("source_type", ""),
                    "chunks": 0,
                    "pages": set(),
                }
            docs[did]["chunks"] += 1
            pn = meta.get("page_number", -1)
            if pn >= 0:
                docs[did]["pages"].add(pn)

        result = []
        for d in docs.values():
            result.append({
                "doc_id": d["doc_id"],
                "filename": d["filename"],
                "source_type": d["source_type"],
                "chunks": d["chunks"],
                "page_count": len(d["pages"]),
            })
        return result

    def delete_document(self, doc_id: str) -> bool:
        """Remove all chunks and pages for a document."""
        # Delete from doc collection
        all_data = self.doc_collection.get(include=["metadatas"])
        doc_ids_to_delete = [
            id_ for id_, meta in zip(all_data["ids"], all_data["metadatas"])
            if meta.get("doc_id") == doc_id
        ]

        # Delete from pages collection
        page_ids_to_delete = []
        if self.pages_collection.count() > 0:
            all_pages = self.pages_collection.get(include=["metadatas"])
            page_ids_to_delete = [
                id_ for id_, meta in zip(all_pages["ids"], all_pages["metadatas"])
                if meta.get("doc_id") == doc_id
            ]

        if not doc_ids_to_delete and not page_ids_to_delete:
            return False

        if doc_ids_to_delete:
            self.doc_collection.delete(ids=doc_ids_to_delete)
        if page_ids_to_delete:
            self.pages_collection.delete(ids=page_ids_to_delete)

        logger.info("Deleted %d chunks + %d pages for doc %s",
                     len(doc_ids_to_delete), len(page_ids_to_delete), doc_id)
        return True

    def delete_session_conversations(self, session_id: str):
        """Delete all conversation entries for a session."""
        if self.conv_collection.count() == 0:
            return
        all_data = self.conv_collection.get(include=["metadatas"])
        ids_to_delete = [
            id_ for id_, meta in zip(all_data["ids"], all_data["metadatas"])
            if meta.get("session_id") == session_id
        ]
        if ids_to_delete:
            self.conv_collection.delete(ids=ids_to_delete)
            logger.info("Deleted %d conversation entries for session %s", len(ids_to_delete), session_id)

    # --- Helpers ---

    @staticmethod
    def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
        """Split text into overlapping chunks."""
        text = text.strip()
        if not text:
            return []
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start += chunk_size - overlap
        return chunks

    @staticmethod
    def _parse_pdf(file_path: str) -> list[tuple[int, str]]:
        """Extract text from a PDF file, returning (1-indexed page_number, page_text) tuples."""
        import pymupdf
        doc = pymupdf.open(file_path)
        pages = []
        for i, page in enumerate(doc):
            text = page.get_text()
            if text.strip():
                pages.append((i + 1, text))
        doc.close()
        return pages

    @staticmethod
    def _parse_pdf_bytes(data: bytes) -> list[tuple[int, str]]:
        """Extract text from PDF bytes, returning (1-indexed page_number, page_text) tuples."""
        import pymupdf
        doc = pymupdf.open(stream=data, filetype="pdf")
        pages = []
        for i, page in enumerate(doc):
            text = page.get_text()
            if text.strip():
                pages.append((i + 1, text))
        doc.close()
        return pages


rag_service = RAGService()
