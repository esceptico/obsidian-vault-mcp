import os
import tempfile
import unittest
from pathlib import Path

from obsidian_mcp.core.config import EmbeddingSettings, VaultSettings
from obsidian_mcp.markdown.frontmatter import patch_frontmatter, split_frontmatter
from obsidian_mcp.core.types import DeleteStrategy, ListSortBy, SearchMode, SortOrder
from obsidian_mcp.vault.service import Vault


class FrontmatterTests(unittest.TestCase):
    def test_frontmatter_round_trip_patch(self) -> None:
        content = "---\ntags:\n- project\nstatus: old\n---\nBody"
        updated = patch_frontmatter(content, {"status": "active", "owner": "me"})
        frontmatter, body = split_frontmatter(updated)

        self.assertEqual(frontmatter["tags"], ["project"])
        self.assertEqual(frontmatter["status"], "active")
        self.assertEqual(frontmatter["owner"], "me")
        self.assertEqual(body, "Body")


class VaultTests(unittest.TestCase):
    def make_vault(self) -> tuple[tempfile.TemporaryDirectory[str], Vault]:
        tmp = tempfile.TemporaryDirectory()
        vault = Vault(VaultSettings(root=Path(tmp.name)), embeddings=None)
        return tmp, vault

    # ----- helpers so the tests stay readable without piling on defaults -----

    def _create(self, vault: Vault, path: str, content: str = "", frontmatter=None) -> dict:
        return vault.create_note(path, content, frontmatter, overwrite=False)

    def _trash(self, vault: Vault, path: str) -> dict:
        return vault.delete_path(path, recursive=False, strategy=DeleteStrategy.TRASH)

    def _list(
        self,
        vault: Vault,
        path: str = "",
        sort_by: ListSortBy = ListSortBy.NAME,
        sort_order: SortOrder = SortOrder.ASC,
    ) -> list[dict]:
        return vault.list(path, sort_by, sort_order)

    def _bm25(self, vault: Vault, query: str) -> dict:
        return vault.search(query, limit=10, mode=SearchMode.BM25)

    def _move(self, vault: Vault, src: str, dst: str, *, rewrite_links: bool = True) -> dict:
        return vault.move_path(src, dst, rewrite_links=rewrite_links, overwrite=False)

    # --------------------------------- tests ---------------------------------

    def test_rejects_path_traversal(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            with self.assertRaises(ValueError):
                vault.read("../outside.md")

    def test_create_read_search(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            self._create(vault, "Projects/Alpha", "This note discusses semantic search.", {"tags": ["ai"]})

            note = vault.read("Projects/Alpha.md")
            self.assertEqual(note["frontmatter"]["tags"], ["ai"])
            self.assertIn("semantic", note["body"])
            self.assertIn("file", note)
            self.assertIsInstance(note["file"]["size"], int)
            self.assertIn("modified_at", note["file"])
            self.assertIn("created_at", note["file"])

            results = self._bm25(vault, "semantic search")
            self.assertEqual(results["hits"][0]["path"], "Projects/Alpha.md")
            self.assertTrue((Path(tmp.name) / ".obsidian-mcp" / "index.sqlite").exists())
            self.assertNotIn(".obsidian-mcp", {entry["path"] for entry in self._list(vault)})

            with self.assertRaises(ValueError):
                self._create(vault, ".obsidian-mcp/manual", "nope")

            with self.assertRaises(ValueError):
                self._create(vault, ".trash/manual", "nope")

    def test_rename_rewrites_wikilinks(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            self._create(vault, "Old Note", "Body")
            self._create(vault, "Ref", "See [[Old Note|alias]] and [[Old Note#Heading]].")

            result = self._move(vault, "Old Note.md", "New Note.md")
            ref = vault.read("Ref.md")

            self.assertEqual(result["rewritten_files"], 1)
            self.assertIn("[[New Note|alias]]", ref["content"])
            self.assertIn("[[New Note#Heading]]", ref["content"])

    def test_nested_dot_trash_is_visible(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            nested = Path(tmp.name) / "Projects" / ".trash" / "note.md"
            nested.parent.mkdir(parents=True)
            nested.write_text("hello", encoding="utf-8")
            listed = self._list(vault, "Projects/.trash")
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["path"], "Projects/.trash/note.md")

    def test_top_level_trash_is_hidden(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            (Path(tmp.name) / ".trash").mkdir()
            (Path(tmp.name) / ".trash" / "x.md").write_text("x", encoding="utf-8")
            self.assertNotIn(".trash", {entry["path"] for entry in self._list(vault)})

    def test_list_sort_by_name_keeps_directories_first(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            (Path(tmp.name) / "Beta").mkdir()
            (Path(tmp.name) / "Alpha").mkdir()
            self._create(vault, "zeta", "z")
            self._create(vault, "aardvark", "a")

            asc = [entry["path"] for entry in self._list(vault, sort_by=ListSortBy.NAME, sort_order=SortOrder.ASC)]
            desc = [entry["path"] for entry in self._list(vault, sort_by=ListSortBy.NAME, sort_order=SortOrder.DESC)]

            self.assertEqual(asc, ["Alpha", "Beta", "aardvark.md", "zeta.md"])
            self.assertEqual(desc, ["Beta", "Alpha", "zeta.md", "aardvark.md"])

    def test_list_sort_by_modified_desc(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            self._create(vault, "Old", "old")
            self._create(vault, "New", "new")
            old = Path(tmp.name) / "Old.md"
            new = Path(tmp.name) / "New.md"
            os.utime(old, (100, 100))
            os.utime(new, (200, 200))

            paths = [
                entry["path"]
                for entry in self._list(vault, sort_by=ListSortBy.MODIFIED_AT, sort_order=SortOrder.DESC)
                if entry["path"] in {"Old.md", "New.md"}
            ]

            self.assertEqual(paths, ["New.md", "Old.md"])

    def test_list_entries_include_file_metadata(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            self._create(vault, "Note", "body")
            entry = next(entry for entry in self._list(vault) if entry["path"] == "Note.md")
            self.assertIsInstance(entry["size"], int)
            self.assertIn("modified_at", entry)
            self.assertIn("created_at", entry)

    def test_delete_refuses_obsidian_mcp(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            self._create(vault, "Note", "x")
            with self.assertRaises(ValueError):
                vault.delete_path(".obsidian-mcp", recursive=True, strategy=DeleteStrategy.DELETE)

    def test_trash_does_not_overwrite_same_second(self) -> None:
        from datetime import datetime, timezone
        from unittest.mock import patch

        tmp, vault = self.make_vault()
        with tmp:
            self._create(vault, "A", "first")
            fixed = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)

            class FrozenDateTime:
                @classmethod
                def now(cls, tz=None):
                    return fixed

                @staticmethod
                def fromtimestamp(ts, tz=None):
                    return datetime.fromtimestamp(ts, tz)

            with patch("obsidian_mcp.vault.service.datetime", FrozenDateTime):
                r1 = self._trash(vault, "A.md")
                self._create(vault, "A", "second")
                r2 = self._trash(vault, "A.md")

            self.assertNotEqual(r1["trashed_to"], r2["trashed_to"])
            trash_dir = Path(tmp.name) / ".trash"
            self.assertEqual(len(list(trash_dir.iterdir())), 2)

    def test_backlinks_missing_path_raises(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            with self.assertRaises(FileNotFoundError):
                vault.backlinks("does-not-exist.md")

    def test_create_note_rejects_oversized_content(self) -> None:
        from obsidian_mcp.core.constants import MAX_NOTE_BYTES

        tmp, vault = self.make_vault()
        with tmp:
            with self.assertRaises(ValueError):
                self._create(vault, "Big", "x" * (MAX_NOTE_BYTES + 1))

    def test_create_note_rejects_oversized_rendered_note(self) -> None:
        from unittest.mock import patch

        tmp, vault = self.make_vault()
        with tmp, patch("obsidian_mcp.vault.service.MAX_NOTE_BYTES", 20):
            with self.assertRaises(ValueError):
                self._create(vault, "Big", "body", {"long": "x" * 40})

    def test_update_note_rejects_oversized_rendered_note(self) -> None:
        from unittest.mock import patch

        tmp, vault = self.make_vault()
        with tmp, patch("obsidian_mcp.vault.service.MAX_NOTE_BYTES", 40):
            self._create(vault, "Note", "body")
            with self.assertRaises(ValueError):
                vault.update_note("Note.md", content=None, frontmatter_patch={"long": "x" * 80})

    def test_sync_from_disk_picks_up_out_of_band_changes(self) -> None:
        """Editing a note directly on disk (e.g. from Obsidian Desktop) goes
        invisible to search until sync_from_disk runs. vault_reindex / startup
        sync are the documented ways to pick it up."""
        tmp, vault = self.make_vault()
        with tmp:
            self._create(vault, "Note", "original body")
            # Tamper directly on disk, bypassing the Vault API.
            (Path(tmp.name) / "Note.md").write_text("tampered cucumber", encoding="utf-8")

            # Search before sync: tampered content not yet indexed.
            before = self._bm25(vault, "cucumber")["hits"]
            self.assertEqual(before, [])

            summary = vault.sync_from_disk()
            self.assertEqual(summary["modified"], 1)

            after = self._bm25(vault, "cucumber")["hits"]
            self.assertEqual(after[0]["path"], "Note.md")

    def test_sync_from_disk_removes_files_deleted_out_of_band(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            self._create(vault, "Note", "body")
            (Path(tmp.name) / "Note.md").unlink()

            summary = vault.sync_from_disk()
            self.assertEqual(summary["removed"], 1)
            self.assertEqual(self._bm25(vault, "body")["hits"], [])

    def test_atomic_write_unique_tmp_and_fsync(self) -> None:
        import os
        from unittest.mock import patch

        tmp, vault = self.make_vault()
        with tmp:
            called: dict[str, int] = {}
            real_fsync = os.fsync

            def spy_fsync(fd: int) -> None:
                called["count"] = called.get("count", 0) + 1
                return real_fsync(fd)

            with patch("obsidian_mcp.vault.service.os.fsync", side_effect=spy_fsync):
                self._create(vault, "Note", "hello")

            self.assertGreaterEqual(called.get("count", 0), 1)

            from obsidian_mcp.vault.paths import temporary_write_path
            a = temporary_write_path(Path(tmp.name) / "X.md")
            b = temporary_write_path(Path(tmp.name) / "X.md")
            self.assertNotEqual(a.name, b.name)

    def test_rename_preserves_folder_qualifier_on_collision(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            self._create(vault, "Projects/Old", "Body")
            self._create(vault, "Archive/New", "Other body")
            self._create(vault, "Ref", "See [[Projects/Old]] and [[Archive/New]].")
            result = self._move(vault, "Projects/Old.md", "Projects/New.md")
            ref = vault.read("Ref.md")

            self.assertEqual(result["rewritten_files"], 1)
            self.assertIn("[[Projects/New]]", ref["content"])
            self.assertIn("[[Archive/New]]", ref["content"])

    def test_rename_preserves_folder_qualifier_when_source_was_qualified(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            self._create(vault, "Projects/Old", "Body")
            self._create(vault, "Ref", "See [[Projects/Old]].")
            self._move(vault, "Projects/Old.md", "Projects/Renamed.md")
            ref = vault.read("Ref.md")
            self.assertIn("[[Projects/Renamed]]", ref["content"])

    def test_move_rolls_back_on_rewrite_failure(self) -> None:
        from unittest.mock import patch

        tmp, vault = self.make_vault()
        with tmp:
            self._create(vault, "Old", "body")
            self._create(vault, "Ref", "[[Old]]")
            with patch.object(vault, "_atomic_write", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    self._move(vault, "Old.md", "New.md")
            self.assertTrue((Path(tmp.name) / "Old.md").exists())
            self.assertFalse((Path(tmp.name) / "New.md").exists())
            ref_text = (Path(tmp.name) / "Ref.md").read_text(encoding="utf-8")
            self.assertIn("[[Old]]", ref_text)

    def test_rename_self_linking_note_does_not_recreate_old_path(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            self._create(vault, "Old", "See [[Old]].")

            result = self._move(vault, "Old.md", "New.md")

            self.assertFalse((Path(tmp.name) / "Old.md").exists())
            self.assertTrue((Path(tmp.name) / "New.md").exists())
            self.assertEqual(result["rewritten_files"], 1)
            self.assertIn("[[New]]", vault.read("New.md")["content"])

    def test_overwrite_move_does_not_restore_old_destination_content(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            self._create(vault, "Old", "moved body")
            self._create(vault, "New", "existing [[Old]]")

            vault.move_path("Old.md", "New.md", rewrite_links=True, overwrite=True)

            self.assertEqual((Path(tmp.name) / "New.md").read_text(encoding="utf-8"), "moved body")

    def test_move_refuses_reserved_trash_path(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            self._create(vault, "Note", "body")
            with self.assertRaises(ValueError):
                vault.move_path("Note.md", ".trash/Note.md", rewrite_links=True, overwrite=False)

    def test_custom_nested_trash_path_is_reserved(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        with tmp:
            vault = Vault(VaultSettings(root=Path(tmp.name), trash_path="System/Trash"), embeddings=None)
            with self.assertRaises(ValueError):
                self._create(vault, "System/Trash/Note", "body")
            visible = self._list(vault)
            self.assertEqual(visible, [])

    def test_embedding_failure_does_not_fail_successful_write(self) -> None:
        from unittest.mock import patch

        tmp = tempfile.TemporaryDirectory()
        with tmp:
            vault = Vault(
                VaultSettings(root=Path(tmp.name)),
                EmbeddingSettings(api_key="k", model="text-embedding-3-small"),
            )
            with patch.object(vault._index, "_embed_texts", side_effect=RuntimeError("openai down")):
                result = self._create(vault, "Note", "lexical body")

            self.assertEqual(result["path"], "Note.md")
            self.assertTrue((Path(tmp.name) / "Note.md").exists())
            self.assertEqual(self._bm25(vault, "lexical")["hits"][0]["path"], "Note.md")

    def test_folder_qualified_wikilinks_are_matched(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            self._create(vault, "Projects/Old Note", "Body")
            self._create(vault, "Ref", "See [[Projects/Old Note]].")

            result = self._move(vault, "Projects/Old Note.md", "Projects/New Note.md")
            ref = vault.read("Ref.md")

            self.assertEqual(result["rewritten_files"], 1)
            self.assertIn("[[Projects/New Note]]", ref["content"])


if __name__ == "__main__":
    unittest.main()
