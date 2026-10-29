"""File-based application settings; credentials never enter session records."""

import json
import os
import re
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, ValidationError, field_validator

from .ai.types import ModelSpec


Api = Literal["chat-completions", "anthropic-messages"]
Auth = Literal["bearer", "x-api-key", "none"]


class ConfiguredModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    id: str = Field(min_length=1)
    name: Optional[str] = None
    context_window: int = Field(default=1_000_000, ge=1024)
    max_output_tokens: int = Field(default=32768, ge=1)
    supports_images: bool = False

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("Model ID must not contain whitespace")
        return value


class ProviderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: Optional[str] = None
    api: Api = "chat-completions"
    base_url: str = Field(min_length=1)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    auth: Optional[Auth] = None
    headers: dict[str, SecretStr] = Field(default_factory=dict)
    extra_body: dict[str, JsonValue] = Field(default_factory=dict)
    include_usage: bool = True
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    models: list[ConfiguredModel] = Field(min_length=1)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("base_url must be an HTTP or HTTPS URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Put credentials in api_key/headers; base_url cannot contain credentials, query or fragment")
        return value.rstrip("/")

    @field_validator("models")
    @classmethod
    def unique_models(cls, value: list[ConfiguredModel]) -> list[ConfiguredModel]:
        if len({model.id for model in value}) != len(value):
            raise ValueError("Duplicate model ID in provider")
        return value

    @field_validator("extra_body")
    @classmethod
    def protect_context(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if {"model", "messages", "tools", "stream", "system"}.intersection(value):
            raise ValueError("extra_body cannot replace model, messages, tools, stream or system")
        return value

    @property
    def auth_type(self) -> Auth:
        return self.auth or ("x-api-key" if self.api == "anthropic-messages" else "bearer")


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    default_model: Optional[str] = None
    providers: dict[str, ProviderSettings] = Field(default_factory=dict)

    @field_validator("providers")
    @classmethod
    def validate_providers(cls, value: dict[str, ProviderSettings]) -> dict[str, ProviderSettings]:
        if any(not re.fullmatch(r"[a-zA-Z0-9_-]+", name) or name == "fake" for name in value):
            raise ValueError("Provider IDs must contain letters, digits, _ or -, and cannot be fake")
        return value

    def model_specs(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id=model.id, name=model.name or model.id, provider=provider_id,
                api=provider.api, base_url=provider.base_url,
                context_window=model.context_window, max_output_tokens=model.max_output_tokens,
                supports_images=model.supports_images,
            )
            for provider_id, provider in self.providers.items() for model in provider.models
        ]

    def resolve(self, reference: Optional[str] = None, *, provider_id: Optional[str] = None) -> ModelSpec:
        models = self.model_specs()
        requested = reference or self.default_model
        if reference is None and provider_id is not None and not any(
            model.provider == provider_id and requested in (model.id, f"{model.provider}/{model.id}") for model in models
        ):
            requested = next((f"{model.provider}/{model.id}" for model in models if model.provider == provider_id), None)
        if requested is None and models:
            requested = f"{models[0].provider}/{models[0].id}"
        candidates = [
            model for model in models
            if requested in (model.id, f"{model.provider}/{model.id}")
            and (provider_id is None or model.provider == provider_id)
        ]
        if len(candidates) != 1:
            raise ValueError("模型未配置或名称不唯一，请使用 /model 查看，并指定 供应商/模型ID。")
        return candidates[0]


def example_settings() -> dict[str, JsonValue]:
    return {
        "default_model": "deepseek/deepseek-flash",
        "providers": {
            "deepseek": {
                "name": "DeepSeek", "api": "chat-completions",
                "base_url": "https://api.deepseek.com", "api_key": "",
                "extra_body": {"thinking": {"type": "enabled"}, "reasoning_effort": "max"},
                "models": [{"id": "deepseek-flash", "name": "DeepSeek V4.1 Flash", "supports_images": True}],
            },
        },
    }


class SettingsStore:
    def __init__(self, cwd: Path, path: Optional[Path] = None) -> None:
        self.global_path = Path.home() / ".citrus-code" / "settings.json"
        self.path = path.expanduser().resolve() if path is not None else self.global_path
        project_path = cwd / ".citrus-code" / "settings.json"
        self.paths = [self.path] if path is not None else list(dict.fromkeys([self.global_path, project_path]))

    def initialize(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation preserves an existing user's credentials and edits.
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            json.dump(example_settings(), file, ensure_ascii=False, indent=2)
            file.write("\n")
        return self.path

    def load(self) -> AppSettings:
        merged: dict[str, object] = {}
        providers: dict[str, object] = {}
        found = False
        for path in self.paths:
            if not path.exists():
                continue
            found = True
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict):
                    raise ValueError("Expected an object")
                unknown = set(data) - {"default_model", "providers"}
                if unknown or not isinstance(data.get("providers", {}), dict):
                    raise ValueError("Invalid settings structure")
                if "default_model" in data:
                    merged["default_model"] = data["default_model"]
                for name, settings in data.get("providers", {}).items():
                    if not isinstance(settings, dict):
                        raise ValueError("Expected provider settings")
                    previous = providers.get(name, {})
                    if not isinstance(previous, dict):
                        raise ValueError("Expected provider settings")
                    providers[name] = {**previous, **settings}
            except (ValueError, UnicodeError):
                raise ValueError(f"配置文件格式无效：{path}；请参考 docs/models.md。") from None

        if not found and os.getenv("DEEPSEEK_API_KEY", "").strip():
            # Optional legacy fallback; all normal use can be configured in JSON.
            merged["default_model"] = "deepseek/" + os.getenv("CITRUS_CODE_MODEL", "deepseek-flash")
            providers["deepseek"] = {
                "api": "chat-completions", "base_url": os.getenv("CITRUS_CODE_BASE_URL", "https://api.deepseek.com"),
                "api_key": os.environ["DEEPSEEK_API_KEY"],
                "extra_body": {"thinking": {"type": "disabled"}},
                "models": [{"id": os.getenv("CITRUS_CODE_MODEL", "deepseek-flash"), "supports_images": os.getenv("CITRUS_CODE_MODEL", "deepseek-flash") in {"deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}}],
            }
        merged["providers"] = providers
        try:
            return AppSettings.model_validate(merged)
        except ValidationError:
            raise ValueError("模型配置校验失败；请检查供应商、api、base_url 和 models，参考 docs/models.md。") from None


def provider_credentials(settings: ProviderSettings) -> tuple[SecretStr, dict[str, str]]:
    headers = {name: value.get_secret_value() for name, value in settings.headers.items()}
    key = settings.api_key.get_secret_value().strip()
    has_auth = any(name.lower() in ("authorization", "x-api-key") and value for name, value in headers.items())
    if settings.auth_type != "none" and not key and not has_auth:
        raise ValueError("供应商缺少 api_key 或认证请求头，请在 settings.json 中填写。")
    return SecretStr(key), headers
