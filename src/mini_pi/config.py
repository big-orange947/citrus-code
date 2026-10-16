import os

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ModelConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: SecretStr


def load_connection_config() -> ModelConnectionConfig:
    api_key = os.getenv("DEEPSEEK_API_KEY")

    if api_key is None or not api_key.strip():
        raise ValueError(
            "Please set the DEEPSEEK_API_KEY environment variable"
        )

    return ModelConnectionConfig(
        provider_id="deepseek",
        model_id=os.getenv(
            "MINI_PI_MODEL",
            "deepseek-flash",
        ),
        base_url=os.getenv(
            "MINI_PI_BASE_URL",
            "https://api.deepseek.com",
        ),
        api_key=SecretStr(api_key),
    )