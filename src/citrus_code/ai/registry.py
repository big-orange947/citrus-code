from .provider import Provider
from .types import ModelSpec


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str,Provider] = {}

    def register(self,provider: Provider) -> None:
        if provider.id in self._providers:
            raise ValueError(
                f"Provider already registered: {provider.id}"
            )
        self._providers[provider.id]=provider

    def get_provider(self,provider_id: str) -> Provider:
        provider = self._providers.get(provider_id)

        if provider is None:
            raise KeyError(
                f"Provider not found: {provider_id}"
            ) 

        return provider

    def get_model(
            self,
            provider_id: str,
            model_id: str,
    ) -> ModelSpec:
        provider = self.get_provider(provider_id)

        for model in provider.get_models():
            if model.id == model_id:
                return model

        raise KeyError(
            f"Model not found: {provider_id}/{model_id}"
        )
        