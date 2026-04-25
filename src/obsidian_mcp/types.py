from enum import StrEnum


class SearchMode(StrEnum):
    BM25 = "bm25"
    HYBRID = "hybrid"
    VECTOR = "vector"


class DeleteStrategy(StrEnum):
    TRASH = "trash"
    DELETE = "delete"


class EntryKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"


class HitSource(StrEnum):
    FTS = "fts"
    VECTOR = "vector"
    HYBRID = "hybrid"
