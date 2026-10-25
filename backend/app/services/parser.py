"""Layout-aware document parsing.

PDFs are parsed with PyMuPDF block analysis: tables are detected and
serialized as Markdown (preserving row/column structure), headings are
detected via font-size heuristics, and reading order is preserved.
CSVs/XLSX are converted to Markdown tables so the LLM sees real structure
instead of comma soup.
"""
import csv
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF


@dataclass
class ParsedBlock:
    text: str
    page: int | None = None
    block_type: str = "paragraph"  # paragraph | heading | table
    meta: dict = field(default_factory=dict)


def _table_to_markdown(table) -> str:
    rows = table.extract()
    if not rows:
        return ""
    header = rows[0]
    md = ["| " + " | ".join(str(c or "") for c in header) + " |",
          "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows[1:]:
        md.append("| " + " | ".join(str(c or "").replace("\n", " ") for c in row) + " |")
    return "\n".join(md)


def parse_pdf(path: str) -> list[ParsedBlock]:
    doc = fitz.open(path)
    blocks: list[ParsedBlock] = []

    for page_num, page in enumerate(doc, start=1):
        # 1. Detect and extract tables first so we don't double-ingest their text
        table_rects = []
        try:
            for table in page.find_tables():
                md = _table_to_markdown(table)
                if md:
                    blocks.append(ParsedBlock(md, page_num, "table"))
                    table_rects.append(fitz.Rect(table.bbox))
        except Exception:
            pass  # table detection is best-effort

        # 2. Extract remaining text with font-size heading heuristics
        page_dict = page.get_text("dict")
        sizes = [s["size"] for b in page_dict["blocks"] if b.get("type") == 0
                 for line in b["lines"] for s in line["spans"]]
        body_size = sorted(sizes)[len(sizes) // 2] if sizes else 11.0

        for b in page_dict["blocks"]:
            if b.get("type") != 0:
                continue
            rect = fitz.Rect(b["bbox"])
            if any(rect.intersects(tr) for tr in table_rects):
                continue  # already captured as a table
            text = " ".join(
                s["text"] for line in b["lines"] for s in line["spans"]
            ).strip()
            if not text:
                continue
            max_size = max(s["size"] for line in b["lines"] for s in line["spans"])
            btype = "heading" if max_size > body_size * 1.2 and len(text) < 200 else "paragraph"
            blocks.append(ParsedBlock(text, page_num, btype))

    doc.close()
    return blocks


ROWS_PER_CHUNK = 30


def _rows_to_markdown(header: list, rows: list[list]) -> str:
    def cell(v):
        return "" if v is None else str(v).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(cell(h) for h in header) + " |",
             "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def _read_rows(path: str) -> list[list]:
    if Path(path).suffix.lower() == ".csv":
        with open(path, newline="", encoding="utf-8", errors="ignore") as f:
            return [row for row in csv.reader(f)]
    from openpyxl import load_workbook  # lazy: only needed for spreadsheets
    wb = load_workbook(path, read_only=True, data_only=True)
    rows = [list(r) for r in wb.active.iter_rows(values_only=True)]
    wb.close()
    return rows


def parse_tabular(path: str) -> list[ParsedBlock]:
    """Spreadsheets become Markdown row-groups. The header row is repeated on
    every group so each chunk is self-describing when retrieved on its own."""
    rows = [r for r in _read_rows(path) if any(v not in (None, "") for v in r)]
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    blocks = []
    for start in range(0, len(body), ROWS_PER_CHUNK):
        segment = body[start:start + ROWS_PER_CHUNK]
        blocks.append(ParsedBlock(_rows_to_markdown(header, segment), None, "table",
                                  {"rows": f"{start + 1}-{start + len(segment)}"}))
    return blocks


def parse_text(path: str) -> list[ParsedBlock]:
    content = Path(path).read_text(errors="ignore")
    return [ParsedBlock(chunk.strip(), None, "paragraph")
            for chunk in content.split("\n\n") if chunk.strip()]


def parse_document(path: str, mime_type: str) -> list[ParsedBlock]:
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf" or "pdf" in mime_type:
        return parse_pdf(path)
    if suffix in (".csv", ".xlsx"):
        return parse_tabular(path)
    return parse_text(path)
