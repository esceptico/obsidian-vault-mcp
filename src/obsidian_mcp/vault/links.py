from collections.abc import Callable
from pathlib import Path

from obsidian_mcp.markdown.obsidian import rewrite_wikilink_targets


def link_names_for(relative_path: str, stem: str, suffix: str) -> set[str]:
    names = {stem}
    if suffix == ".md":
        names.add(Path(relative_path).with_suffix("").as_posix())
        names.add(relative_path)
    return names


def plan_wikilink_rewrites(
    *,
    root: Path,
    src: Path,
    dst: Path,
    old_names: set[str],
    relative_str: Callable[[Path], str],
    is_ignored: Callable[[Path], bool],
) -> list[tuple[Path, str]]:
    new_bare = dst.stem
    new_qualified = Path(relative_str(dst)).with_suffix("").as_posix()
    same_stem_exists = _same_stem_note_exists(root, src, dst, new_bare, is_ignored)

    def replacement_for(matched_old: str) -> str:
        if "/" in matched_old or same_stem_exists:
            return new_qualified
        return new_bare

    pending: list[tuple[Path, str]] = []
    for path in root.rglob("*.md"):
        if not path.is_file() or is_ignored(path):
            continue
        original = path.read_text(encoding="utf-8")
        updated = rewrite_wikilink_targets(original, old_names, replacement_for)
        if updated != original:
            pending.append((path, updated))
    return pending


def _same_stem_note_exists(
    root: Path,
    src: Path,
    dst: Path,
    new_bare: str,
    is_ignored: Callable[[Path], bool],
) -> bool:
    return any(
        other.is_file()
        and other.suffix == ".md"
        and other.stem == new_bare
        and other.resolve() != src.resolve()
        and other.resolve() != dst.resolve()
        and not is_ignored(other)
        for other in root.rglob("*.md")
    )
