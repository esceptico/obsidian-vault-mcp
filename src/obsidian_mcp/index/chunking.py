import hashlib
import re
from dataclasses import dataclass

from obsidian_mcp.core.constants import (
    EMBEDDING_CHUNK_OVERLAP_CHARS,
    EMBEDDING_CHUNK_TARGET_CHARS,
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_BREAK_RE = re.compile(
    r"\n\s*\n|\n(?=\s*(?:[-*+]\s|\d+[.)]\s|\[|#{1,6}\s))|(?<=[.!?;])\s+"
)
_BLOCK_START_RE = re.compile(r"(?:[-*+]\s|\d+[.)]\s|\[|#{1,6}\s)")


@dataclass(frozen=True)
class TextChunk:
    chunk_index: int
    heading_path: str
    text: str
    start_char: int
    end_char: int

    @property
    def chunk_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Section:
    heading_path: str
    text: str
    start_char: int


def chunk_markdown(body: str, *, body_start: int = 0) -> tuple[TextChunk, ...]:
    chunks: list[TextChunk] = []
    for section in _sections(body, body_start):
        for text, start, end in _split_section(section.text, section.start_char):
            stripped = text.strip()
            if not stripped:
                continue
            leading = len(text) - len(text.lstrip())
            trailing = len(text.rstrip())
            chunks.append(
                TextChunk(
                    chunk_index=len(chunks),
                    heading_path=section.heading_path,
                    text=stripped,
                    start_char=start + leading,
                    end_char=start + trailing,
                )
            )
    if chunks:
        return _drop_nonleaf_heading_chunks(chunks)
    stripped = body.strip()
    if not stripped:
        return (
            TextChunk(
                chunk_index=0,
                heading_path="",
                text="",
                start_char=body_start,
                end_char=body_start,
            ),
        )
    leading = len(body) - len(body.lstrip())
    trailing = len(body.rstrip())
    return (
        TextChunk(
            chunk_index=0,
            heading_path="",
            text=stripped,
            start_char=body_start + leading,
            end_char=body_start + trailing,
        ),
    )


def _sections(body: str, body_start: int) -> list[Section]:
    headings: list[str] = []
    sections: list[Section] = []
    section_start = 0
    section_heading = ""
    in_fence = False
    position = 0

    for line in body.splitlines(keepends=True):
        line_start = position
        position += len(line)
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line.rstrip("\r\n"))
        if match is None:
            continue
        if line_start > section_start:
            sections.append(
                Section(
                    section_heading,
                    body[section_start:line_start],
                    body_start + section_start,
                )
            )
        level = len(match.group(1))
        title = match.group(2).strip()
        headings = headings[: level - 1]
        headings.append(title)
        section_heading = " > ".join(headings)
        section_start = line_start

    if section_start < len(body):
        sections.append(
            Section(section_heading, body[section_start:], body_start + section_start)
        )
    return sections or [Section("", body, body_start)]


def _split_section(text: str, start_char: int) -> list[tuple[str, int, int]]:
    if len(text) <= EMBEDDING_CHUNK_TARGET_CHARS:
        return [(text, start_char, start_char + len(text))]

    chunks = []
    cursor = 0
    while cursor < len(text):
        end = _find_chunk_end(text, cursor)
        if end <= cursor:
            end = min(len(text), cursor + EMBEDDING_CHUNK_TARGET_CHARS)
        chunks.append((text[cursor:end], start_char + cursor, start_char + end))
        if end >= len(text):
            break
        overlap_start = max(cursor + 1, end - EMBEDDING_CHUNK_OVERLAP_CHARS)
        cursor = _find_next_chunk_start(text, overlap_start, end)
    return chunks


def _find_chunk_end(text: str, start: int) -> int:
    hard_end = min(len(text), start + EMBEDDING_CHUNK_TARGET_CHARS)
    soft_start = min(hard_end, start + EMBEDDING_CHUNK_TARGET_CHARS // 2)
    best = None
    for match in _BREAK_RE.finditer(text, soft_start, hard_end):
        best = match.end()
    return best or hard_end


def _find_next_chunk_start(text: str, start: int, previous_end: int) -> int:
    position = start
    while position < previous_end:
        if text[position] != "\n":
            position += 1
            continue
        candidate = position + 1
        while candidate < len(text) and text[candidate] in " \t":
            candidate += 1
        if candidate < previous_end and _BLOCK_START_RE.match(text[candidate:]):
            return candidate
        position += 1
    return start


def _drop_nonleaf_heading_chunks(chunks: list[TextChunk]) -> tuple[TextChunk, ...]:
    kept = []
    for index, chunk in enumerate(chunks):
        next_chunk = chunks[index + 1] if index + 1 < len(chunks) else None
        if (
            next_chunk
            and _is_heading_only(chunk.text)
            and _is_child_heading(chunk.heading_path, next_chunk.heading_path)
        ):
            continue
        kept.append(chunk)
    return tuple(
        TextChunk(
            chunk_index=index,
            heading_path=chunk.heading_path,
            text=chunk.text,
            start_char=chunk.start_char,
            end_char=chunk.end_char,
        )
        for index, chunk in enumerate(kept)
    )


def _is_heading_only(text: str) -> bool:
    return _HEADING_RE.match(text.strip()) is not None


def _is_child_heading(parent: str, child: str) -> bool:
    return bool(parent) and child.startswith(f"{parent} > ")
