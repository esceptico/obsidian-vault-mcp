from dataclasses import dataclass
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
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
    model: str = "text-embedding-3-small"
    dimensions: int | None = None
    batch_size: int = 64

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    vault_root: Path = Field(validation_alias="OBSIDIAN_MCP_VAULT_ROOT")
    trash_path: str = Field(default=".trash", validation_alias="OBSIDIAN_MCP_TRASH_PATH")
    default_note_folder: str = Field(default="Inbox", validation_alias="OBSIDIAN_MCP_DEFAULT_NOTE_FOLDER")
    daily_notes_path: str = Field(default="Daily", validation_alias="OBSIDIAN_MCP_DAILY_NOTES_PATH")
    attachments_path: str = Field(default="Assets", validation_alias="OBSIDIAN_MCP_ATTACHMENTS_PATH")
    templates_path: str = Field(default="Templates", validation_alias="OBSIDIAN_MCP_TEMPLATES_PATH")

    host: str = Field(default="127.0.0.1", validation_alias=AliasChoices("OBSIDIAN_MCP_HOST", "FASTMCP_HOST"))
    port: int = Field(default=8000, validation_alias=AliasChoices("OBSIDIAN_MCP_PORT", "FASTMCP_PORT"))
    public_url: str | None = Field(default=None, validation_alias="OBSIDIAN_MCP_PUBLIC_URL")
    auth_token: str | None = Field(default=None, validation_alias="OBSIDIAN_MCP_AUTH_TOKEN")

    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OBSIDIAN_MCP_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    embedding_model: str = Field(default="text-embedding-3-small", validation_alias="OBSIDIAN_MCP_EMBEDDING_MODEL")
    embedding_dimensions: int | None = Field(default=None, validation_alias="OBSIDIAN_MCP_EMBEDDING_DIMENSIONS")
    embedding_batch_size: int = Field(default=64, validation_alias="OBSIDIAN_MCP_EMBEDDING_BATCH_SIZE")

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
            api_key=self.openai_api_key.get_secret_value() if self.openai_api_key else None,
            model=self.embedding_model,
            dimensions=self.embedding_dimensions,
            batch_size=self.embedding_batch_size,
        )

    @property
    def resolved_public_url(self) -> str:
        return (self.public_url or f"http://{self.host}:{self.port}").rstrip("/")


def load_settings() -> ServerSettings:
    return ServerSettings()
