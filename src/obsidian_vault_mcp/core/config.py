from dataclasses import dataclass
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class VaultSettings:
    root: Path
    trash_path: str = ".trash"
    default_note_folder: str = "Inbox"
    daily_notes_path: str = "Daily"
    attachments_path: str = "Assets"
    templates_path: str = "Templates"


@dataclass(frozen=True)
class EmbeddingSettings:
    api_key: str | None = None
    base_url: str | None = None
    model: str = "text-embedding-3-small"
    dimensions: int | None = None
    batch_size: int = 64

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_prefix="OBSIDIAN_VAULT_MCP_"
    )

    vault_root: Path = Field()
    trash_path: str = Field(default=".trash")
    default_note_folder: str = Field(default="Inbox")
    daily_notes_path: str = Field(default="Daily")
    attachments_path: str = Field(default="Assets")
    templates_path: str = Field(default="Templates")

    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000, ge=0, le=65535)
    auth_token: str | None = Field(default=None)

    openai_api_key: SecretStr | None = Field(default=None)
    openai_base_url: str | None = Field(default=None)
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_dimensions: int | None = Field(default=None, gt=0)
    embedding_batch_size: int = Field(default=64, ge=1)

    @property
    def vault(self) -> VaultSettings:
        return VaultSettings(
            root=self.vault_root.expanduser(),
            trash_path=self.trash_path,
            default_note_folder=self.default_note_folder,
            daily_notes_path=self.daily_notes_path,
            attachments_path=self.attachments_path,
            templates_path=self.templates_path,
        )

    @property
    def embeddings(self) -> EmbeddingSettings:
        return EmbeddingSettings(
            api_key=self.openai_api_key.get_secret_value()
            if self.openai_api_key
            else None,
            base_url=self.openai_base_url,
            model=self.embedding_model,
            dimensions=self.embedding_dimensions,
            batch_size=self.embedding_batch_size,
        )


def load_settings() -> ServerSettings:
    return ServerSettings()
