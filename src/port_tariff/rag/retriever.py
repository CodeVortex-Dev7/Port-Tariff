import hashlib
import re
from pathlib import Path
from typing import Iterable, List

from langchain_chroma import Chroma
from langchain_core.documents import Document

from port_tariff.logging_config import get_logger, timed
from port_tariff.models.catalog import ChargeCatalog, ChargeEntry
from port_tariff.rag.embeddings import get_embeddings
from port_tariff.rag.splitter import TableAwareMarkdownSplitter

logger = get_logger(__name__)

MAX_REFERENCED_CHUNKS = 5
MAX_CHUNKS_PER_CHARGE = 8
SEARCH_TOP_K = 4


def _ref_matches(ref: str, hint: str) -> bool:
    """True when a catalog section reference (clause number or heading text)
    designates the chunk whose heading line is `hint`."""
    ref = ref.strip()
    if not ref:
        return False
    number = re.search(r"\d+(?:\.\d+)+", ref)
    if number:
        return (
            re.search(rf"(?<![\w.]){re.escape(number.group())}(?!\w)", hint)
            is not None
        )
    return ref.casefold() in hint.casefold()


class TariffRetriever:
    """Catalog-driven retriever: for every discovered charge it gathers the
    sections the catalog points to, then fills up with semantic search using
    the queries generated during discovery. No charge names are hardcoded.
    """

    def __init__(self, persist_dir: Path = Path("data/chroma_db")):
        self.persist_dir = persist_dir
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.embeddings = get_embeddings()
        self.splitter = TableAwareMarkdownSplitter()

    def _get_collection_name(self, document_id: str, markdown_text: str) -> str:
        content_hash = hashlib.sha256(markdown_text.encode("utf-8")).hexdigest()[:12]
        clean_id = "".join(c if c.isalnum() else "_" for c in document_id)
        return f"tariff_{clean_id}_{content_hash}"[:63]

    def index_document(self, document_id: str, markdown_text: str) -> Chroma:
        """Indexes bounded document chunks into ChromaDB."""
        collection_name = self._get_collection_name(document_id, markdown_text)

        vectorstore = Chroma(
            collection_name=collection_name,
            embedding_function=self.embeddings,
            persist_directory=str(self.persist_dir),
        )

        existing = vectorstore._collection.count()
        if existing == 0:
            with timed(
                logger,
                "rag.index.created",
                collection=collection_name,
                document_id=document_id,
            ) as extra:
                docs = self.splitter.split_text(markdown_text, document_id=document_id)
                batch_size = 4
                for i in range(0, len(docs), batch_size):
                    batch = docs[i : i + batch_size]
                    vectorstore.add_documents(batch)
                extra["chunk_count"] = len(docs)
        else:
            logger.info(
                "rag.index.reused",
                collection=collection_name,
                existing_chunks=existing,
            )

        return vectorstore

    @staticmethod
    def _load_chunks(vectorstore: Chroma) -> List[Document]:
        data = vectorstore.get(include=["documents", "metadatas"])
        chunks = [
            Document(page_content=text, metadata=meta or {})
            for text, meta in zip(data["documents"], data["metadatas"])
        ]
        return sorted(chunks, key=lambda d: d.metadata.get("chunk_index", 0))

    def get_chunks(self, document_id: str, markdown_text: str) -> List[Document]:
        """Returns the indexed chunks in document order (used to outline the document)."""
        return self._load_chunks(self.index_document(document_id, markdown_text))

    @staticmethod
    def _select_chunks(
        vectorstore: Chroma, entry: ChargeEntry, chunks: List[Document]
    ) -> List[Document]:
        selected: List[Document] = []
        seen: set = set()

        def take(candidates: Iterable[Document], limit: int) -> None:
            for doc in candidates:
                if len(selected) >= limit:
                    return
                key = hashlib.sha256(doc.page_content.encode("utf-8")).hexdigest()
                if key not in seen:
                    seen.add(key)
                    selected.append(doc)

        # 1) Sections the catalog points to, in document order.
        referenced = (
            chunk
            for chunk in chunks
            if any(
                _ref_matches(ref, chunk.metadata.get("section_hint", ""))
                for ref in entry.section_refs
            )
        )
        take(referenced, MAX_REFERENCED_CHUNKS)

        # 2) Semantic search with the queries generated during discovery.
        for query in entry.search_queries:
            take(
                vectorstore.similarity_search(query, k=SEARCH_TOP_K),
                MAX_CHUNKS_PER_CHARGE,
            )
        return selected

    def retrieve_context(
        self,
        document_id: str,
        markdown_text: str,
        catalog: ChargeCatalog,
        chunks: List[Document],
    ) -> List[str]:
        """Returns one context string per catalog charge (same order as catalog.charges)."""
        vectorstore = self.index_document(document_id, markdown_text)
        contexts: List[str] = []
        for entry in catalog.charges:
            docs = self._select_chunks(vectorstore, entry, chunks)
            contexts.append(
                "\n\n---\n\n".join(
                    f"### [RETRIEVED SECTION: {doc.metadata.get('section_hint', 'Tariff Section')}]\n{doc.page_content}"
                    for doc in docs
                )
            )
            logger.info(
                "rag.retrieve.charge",
                charge=entry.category,
                sections=len(docs),
                chars=len(contexts[-1]),
            )
        return contexts
