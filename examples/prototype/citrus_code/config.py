from dataclasses import dataclass
import os


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    model: str
    api_key: str|None=None
    timeout_seconds: float=60.0

    @classmethod
    def from_env(cls) -> "ModelConfig":
        base_url=os.getenv("CITRUS_CODE_BASE_URL")
        model=os.getenv("CITRUS_CODE_MODEL")
        api_key=os.getenv("CITRUS_CODE_API_KEY")

        if not base_url:
            raise ValueError("Missing environment variable : CITRUS_CODE_BASE_URL")
        if not model:
            raise ValueError("Missing environment variable : CITRUS_CODE_MODEL")

        return cls(
            base_url=base_url.rstrip("/"),
            model=model,
            api_key=api_key,
        )

