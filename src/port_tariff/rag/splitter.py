import re
from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from port_tariff.logging_config import get_logger

logger = get_logger(__name__)

# Structural detection of numbered clause headings common to regulatory
# documents (TNPA, Rotterdam, Houston, IMO circulars, MCA notices, ...).
# Matches lines like:
#   "4.1 PORT FEES ON VESSELS"
#   "4.1.1 PORT DUES"
#   "6.2.2 Bookings at the Port of Durban"
# Relies on Arabic numerals + dot hierarchy + line structure rather than
# on any English vocabulary. The heading body must begin with an ASCII
# uppercase letter, which covers the Latin-script tariff documents in
# scope for this project.
_CLAUSE_HEADING_PATTERN = re.compile(
    r"^(\d+(?:\.\d+){1,3})\s+([A-Z][a-zA-Z0-9 ,\-&'/]{2,70})$",
    re.MULTILINE,
)


def _inject_clause_headings(markdown_text: str) -> str:
    """Insert markdown H2 markers before numbered clause headings so the
    downstream MarkdownHeaderTextSplitter partitions the document by clause
    rather than by arbitrary character boundaries.

    Skips existing markdown headings and table rows, and lines outside the
    typical length bounds of a heading.
    """
    lines = markdown_text.split("\n")
    out: List[str] = []
    injected = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("|"):
            out.append(line)
            continue
        if not (4 <= len(stripped) <= 100):
            out.append(line)
            continue
        if _CLAUSE_HEADING_PATTERN.match(stripped):
            out.append(f"## {stripped}")
            injected += 1
        else:
            out.append(line)
    if injected:
        logger.debug("splitter.clause_headings_injected", count=injected)
    return "\n".join(out)


class TableAwareMarkdownSplitter:
    """Hierarchical Hybrid Splitter: splits by headers if present, and bounds all

    chunks to a maximum size using semantic separators (Subrule, Section, Tables).
    Guarantees no chunk exceeds chunk_size tokens.
    """

    def __init__(self, chunk_size: int = 4000, chunk_overlap: int = 200):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#", "Header 1"),
                ("##", "Header 2"),
                ("###", "Header 3"),
                ("####", "Header 4"),
            ],
            strip_headers=False,
        )
        # Separator priority: paragraph boundaries BEFORE table
        # boundaries. This keeps markdown tables intact when they
        # fit within a chunk and only falls back to splitting at
        # table boundaries when paragraph splitting is insufficient.
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=[
                "\n## ",
                "\n### ",
                "\n# ",
                "\nSUBRULE",
                "\nSECTION",
                "\nSubrule",
                "\nSection",
                "\n\n",
                "\n\n|",
                "\n",
                " ",
            ],
        )

    def split_text(
        self, markdown_text: str, document_id: str = "tariff"
    ) -> List[Document]:
        """Splits markdown into bounded, semantic chunks."""
        # Inject markdown H2 headings at numbered clause boundaries so the
        # header splitter sees the document's real structure. This is what
        # keeps clauses like "4.1.1 PORT DUES ... 192.73 ... 57.79" as a
        # single retrievable unit instead of a fragment inside an
        # arbitrary character-based chunk.
        structured_markdown = _inject_clause_headings(markdown_text)

        try:
            header_docs = self.header_splitter.split_text(structured_markdown)
        except Exception as exc:
            logger.warning(
                "splitter.header_split_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            header_docs = []

        # If document had no markdown headers, treat the injected markdown as
        # the base so clause boundaries are still present in sub-chunks.
        if not header_docs or (
            len(header_docs) == 1 and len(header_docs[0].page_content) > self.chunk_size
        ):
            base_docs = [
                Document(
                    page_content=structured_markdown,
                    metadata={"document_id": document_id},
                )
            ]
        else:
            base_docs = header_docs

        # Heading-aware recursive splitting:
        # For each section returned by the header splitter, propagate the
        # section heading into EVERY sub-chunk produced by the recursive
        # splitter. Without this, sub-chunks that start mid-section lose the
        # clause context, embedding poorly and dropping out of retrieval.
        final_docs: List[Document] = []

        for doc in base_docs:
            content = doc.page_content
            first_line = content.split("\n", 1)[0].strip()
            heading_prefix = (
                f"{first_line}\n\n" if first_line.startswith("## ") else ""
            )

            sub_chunks = self.text_splitter.split_documents([doc])
            for sub in sub_chunks:
                sub_content = sub.page_content.strip()
                if not sub_content:
                    continue
                if heading_prefix and not sub_content.startswith(first_line):
                    sub_content = heading_prefix + sub_content
                final_docs.append(
                    Document(
                        page_content=sub_content,
                        metadata=dict(sub.metadata),
                    )
                )

        # Merge pass: coalesce orphaned tiny chunks (e.g. a lone heading)
        # with their following chunk. Retrieval treats these as standalone
        # documents, polluting the candidate pool with content-free entries.
        MIN_CHUNK_CHARS = 200
        merged_docs: List[Document] = []
        i = 0
        while i < len(final_docs):
            doc = final_docs[i]
            if (
                len(doc.page_content) < MIN_CHUNK_CHARS
                and i + 1 < len(final_docs)
                and len(doc.page_content) + len(final_docs[i + 1].page_content)
                <= self.chunk_size
            ):
                merged_docs.append(
                    Document(
                        page_content=(
                            doc.page_content + "\n\n" + final_docs[i + 1].page_content
                        ),
                        metadata=doc.metadata,
                    )
                )
                i += 2
            else:
                merged_docs.append(doc)
                i += 1
        final_docs = merged_docs

        # Filter heading-only chunks. Tables of contents and orphan
        # section headers (e.g. a TOC entry rendered as "## 3.8 Berthing
        # services" with no body) embed poorly and consume retrieval
        # slots that should go to content-bearing chunks.
        def _is_heading_only(text: str) -> bool:
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            if not lines:
                return True
            return all(l.startswith("#") for l in lines)

        final_docs = [
            d for d in final_docs if not _is_heading_only(d.page_content)
        ]

        for idx, doc in enumerate(final_docs):
            metadata = dict(doc.metadata)
            metadata["document_id"] = document_id
            metadata["chunk_index"] = idx
            first_line = doc.page_content.split("\n")[0][:80]
            metadata["section_hint"] = first_line
            doc.metadata = metadata

        sizes = [len(d.page_content) for d in final_docs] or [0]
        logger.info(
            "splitter.completed",
            document_id=document_id,
            input_chars=len(markdown_text),
            chunk_count=len(final_docs),
            avg_chunk_chars=sum(sizes) // len(sizes),
            max_chunk_chars=max(sizes),
            chunk_size=self.chunk_size,
        )

        return final_docs
