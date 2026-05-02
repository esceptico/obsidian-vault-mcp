from collections.abc import Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    limit: int
    offset: int
    next_offset: int | None
    total: int | None = None

    @property
    def has_more(self) -> bool:
        return self.next_offset is not None

    @property
    def returned(self) -> int:
        return len(self.items)


def validate_page(limit: int, offset: int, max_limit: int) -> None:
    if limit < 1 or limit > max_limit:
        raise ValueError(f"limit must be between 1 and {max_limit}")
    if offset < 0:
        raise ValueError("offset must be greater than or equal to 0")


def page_items(items: Sequence[T], limit: int, offset: int) -> Page[T]:
    total = len(items)
    page = tuple(items[offset : offset + limit])
    next_offset = offset + len(page) if offset + len(page) < total else None
    return Page(
        items=page, limit=limit, offset=offset, next_offset=next_offset, total=total
    )
