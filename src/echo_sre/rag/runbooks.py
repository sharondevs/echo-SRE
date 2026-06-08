"""BM25 retrieval over markdown runbooks.

Keeps the dependency surface tiny (no embedding service / vector DB) while remaining
pluggable: the same ``RunbookIndex.search`` contract could be backed by embeddings in a
later pass. Runbooks are chunked by markdown heading so retrieval returns a focused
section rather than a whole document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel
from rank_bm25 import BM25Okapi

_RUNBOOK_DIR = Path(__file__).parent / "runbooks"
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class RunbookHit(BaseModel):
    title: str
    section: str
    text: str
    score: float


@dataclass
class _Chunk:
    title: str
    section: str
    text: str
    tokens: list[str]


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _chunk_markdown(title: str, body: str) -> list[_Chunk]:
    """Split a runbook into (section, text) chunks at ``##`` headings."""
    chunks: list[_Chunk] = []
    section = "overview"
    buf: list[str] = []

    def flush():
        if buf:
            text = "\n".join(buf).strip()
            if text:
                chunks.append(_Chunk(title, section, text, _tokenize(f"{title} {section} {text}")))

    for line in body.splitlines():
        if line.startswith("## "):
            flush()
            section = line[3:].strip()
            buf = []
        elif line.startswith("# "):
            continue  # document title, already captured
        else:
            buf.append(line)
    flush()
    return chunks


class RunbookIndex:
    def __init__(self, chunks: list[_Chunk]):
        self._chunks = chunks
        self._bm25 = BM25Okapi([c.tokens for c in chunks]) if chunks else None

    @classmethod
    def load(cls, directory: Path | str | None = None) -> "RunbookIndex":
        directory = Path(directory) if directory else _RUNBOOK_DIR
        chunks: list[_Chunk] = []
        for md in sorted(directory.glob("*.md")):
            body = md.read_text()
            first = body.splitlines()[0] if body.splitlines() else md.stem
            title = first[2:].strip() if first.startswith("# ") else md.stem
            chunks.extend(_chunk_markdown(title, body))
        return cls(chunks)

    def search(self, query: str, k: int = 3) -> list[RunbookHit]:
        if not self._bm25 or not self._chunks:
            return []
        scores = self._bm25.get_scores(_tokenize(query))
        ranked = sorted(zip(self._chunks, scores), key=lambda x: x[1], reverse=True)
        hits: list[RunbookHit] = []
        for chunk, score in ranked[:k]:
            if score <= 0:
                continue
            hits.append(
                RunbookHit(title=chunk.title, section=chunk.section, text=chunk.text, score=round(float(score), 3))
            )
        return hits
