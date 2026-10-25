"""Structure-aware chunking.

Instead of fixed-size character splits, chunks follow document structure:
headings start new chunks and are prepended as context; tables are kept
atomic (never split mid-row); paragraphs are packed up to a token budget
with sentence-boundary overflow.
"""
from dataclasses import dataclass

from app.services.parser import ParsedBlock

MAX_CHUNK_CHARS = 1600
OVERLAP_CHARS = 200


@dataclass
class Chunk:
    text: str
    page: int | None
    section: str | None
    chunk_type: str


def chunk_blocks(blocks: list[ParsedBlock]) -> list[Chunk]:
    chunks: list[Chunk] = []
    current_section: str | None = None
    buffer: list[str] = []
    buffer_page: int | None = None

    def flush():
        nonlocal buffer
        if buffer:
            text = "\n".join(buffer).strip()
            if text:
                prefix = f"[Section: {current_section}]\n" if current_section else ""
                chunks.append(Chunk(prefix + text, buffer_page, current_section, "text"))
        buffer = []

    for block in blocks:
        if block.block_type == "heading":
            flush()
            current_section = block.text
            continue
        if block.block_type == "table":
            flush()
            prefix = f"[Section: {current_section}]\n" if current_section else ""
            chunks.append(Chunk(prefix + block.text, block.page, current_section, "table"))
            continue
        # paragraph packing
        if sum(len(b) for b in buffer) + len(block.text) > MAX_CHUNK_CHARS:
            flush()
            # sentence-level overlap from previous chunk for continuity
            if chunks and chunks[-1].chunk_type == "text":
                buffer.append(chunks[-1].text[-OVERLAP_CHARS:])
        if not buffer:
            buffer_page = block.page
        buffer.append(block.text)

    flush()
    return [c for c in chunks if len(c.text) > 40]
