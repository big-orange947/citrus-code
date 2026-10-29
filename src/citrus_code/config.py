from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from .settings import SettingsStore, provider_credentials


class ModelConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: SecretStr


def load_connection_config() -> ModelConnectionConfig:
    settings = SettingsStore(Path.cwd()).load()
    model = settings.resolve(provider_id="deepseek")
    provider = settings.providers[model.provider]
    key, _ = provider_credentials(provider)
    return ModelConnectionConfig(
        provider_id=model.provider, model_id=model.id,
        base_url=model.base_url, api_key=key,
    )
