from obsidian_mcp.index.search import IndexedNote, SearchIndex


def sync_index(index: SearchIndex, on_disk: dict[str, str]) -> dict[str, int]:
    indexed = index.store.all_records()
    added = modified = unchanged = removed = 0

    for rel, content in on_disk.items():
        note = IndexedNote(path=rel, content=content)
        if rel not in indexed:
            index.upsert_note(note, embed=False)
            added += 1
            continue
        if index.content_hash_for(note) == indexed[rel].content_hash:
            unchanged += 1
            continue
        index.upsert_note(note, embed=False)
        modified += 1

    for rel in set(indexed) - set(on_disk):
        index.delete_note(rel)
        removed += 1

    embedded = index.embed_pending()
    return {
        "added": added,
        "modified": modified,
        "removed": removed,
        "unchanged": unchanged,
        "embedded": embedded,
    }
