import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from obsidian_mcp.core.config import ServerSettings


class ConfigTests(unittest.TestCase):
    def test_settings_load_from_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "OBSIDIAN_MCP_VAULT_ROOT": tmp,
                "OBSIDIAN_MCP_AUTH_TOKEN": "secret",
                "OBSIDIAN_MCP_OPENAI_API_KEY": "openai-key",
                "OBSIDIAN_MCP_OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
                "OBSIDIAN_MCP_EMBEDDING_MODEL": "text-embedding-3-large",
                "OBSIDIAN_MCP_EMBEDDING_DIMENSIONS": "256",
            }
            with patch.dict(os.environ, env, clear=True):
                settings = ServerSettings(_env_file=None)  # type: ignore[call-arg]

        self.assertEqual(settings.vault.root, Path(tmp))
        self.assertEqual(settings.auth_token, "secret")
        self.assertEqual(settings.embeddings.api_key, "openai-key")
        self.assertEqual(settings.embeddings.base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(settings.embeddings.model, "text-embedding-3-large")
        self.assertEqual(settings.embeddings.dimensions, 256)

    def test_plain_openai_environment_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "OBSIDIAN_MCP_VAULT_ROOT": tmp,
                "OPENAI_API_KEY": "plain-openai-key",
                "OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
            }
            with patch.dict(os.environ, env, clear=True):
                settings = ServerSettings(_env_file=None)  # type: ignore[call-arg]

        self.assertIsNone(settings.embeddings.api_key)
        self.assertIsNone(settings.embeddings.base_url)

    def test_embedding_batch_size_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "OBSIDIAN_MCP_VAULT_ROOT": tmp,
                "OBSIDIAN_MCP_EMBEDDING_BATCH_SIZE": "0",
            }
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValidationError):
                    ServerSettings(_env_file=None)  # type: ignore[call-arg]


if __name__ == "__main__":
    unittest.main()
