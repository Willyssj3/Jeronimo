"""Proveedores con API compatible con OpenAI: OpenAI, Google Gemini,
OpenRouter, DeepSeek, LM Studio o cualquier servidor compatible."""

from __future__ import annotations

import openai

from .base import (AuthError, Completion, ConnectionProblem, Provider, ProviderError,
                   RateLimitError, Usage, strip_thinking)

# tipo -> (URL base, ¿es local?, modelo sugerido)
PRESETS: dict[str, tuple[str | None, bool, str]] = {
    "openai": ("https://api.openai.com/v1", False, ""),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", False, ""),
    "openrouter": ("https://openrouter.ai/api/v1", False, ""),
    "deepseek": ("https://api.deepseek.com/v1", False, "deepseek-chat"),
    "lmstudio": ("http://localhost:1234/v1", True, ""),
    "compatible": (None, False, ""),
}

NAMES = {
    "openai": "OpenAI",
    "gemini": "Google Gemini",
    "openrouter": "OpenRouter",
    "deepseek": "DeepSeek",
    "lmstudio": "LM Studio",
    "compatible": "el servidor",
}


class OpenAICompatProvider(Provider):
    def __init__(self, kind: str, model: str, *, api_key: str | None = None, base_url: str | None = None,
                 temperature: float | None = None, max_retries: int = 4, timeout: float = 900.0):
        if kind not in PRESETS:
            raise ProviderError(f"Proveedor desconocido: {kind}")
        default_url, local, default_model = PRESETS[kind]
        self.kind = kind
        self.model = model or default_model
        if not self.model:
            raise ProviderError(f"Elige un modelo para {NAMES[kind]}.")
        self.base_url = base_url or default_url
        if not self.base_url:
            raise ProviderError("Indica la dirección (URL) del servidor compatible con OpenAI.")
        if not local and not api_key and kind != "compatible":
            raise AuthError(f"Falta la clave de API de {NAMES[kind]}.")
        self.local = local or self.base_url.startswith(("http://localhost", "http://127.0.0.1"))
        self.temperature = temperature if temperature is not None else (0.2 if self.local else None)
        self.max_output = 4096 if self.local else 16000
        self.client = openai.OpenAI(api_key=api_key or "sin-clave", base_url=self.base_url,
                                    max_retries=max_retries, timeout=timeout)

    def complete(self, system: str, user: str, *, max_tokens: int | None = None) -> Completion:
        kwargs: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        limit = max_tokens or self.max_output
        if self.kind == "openai":
            kwargs["max_completion_tokens"] = limit
        else:
            kwargs["max_tokens"] = limit
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        name = NAMES[self.kind]
        try:
            r = self.client.chat.completions.create(**kwargs)
        except openai.AuthenticationError as e:
            raise AuthError(f"La clave de API de {name} no es válida.") from e
        except openai.PermissionDeniedError as e:
            raise AuthError(f"Tu clave de {name} no tiene permiso para usar «{self.model}».") from e
        except openai.NotFoundError as e:
            raise ProviderError(f"{name} no encuentra el modelo «{self.model}».") from e
        except openai.RateLimitError as e:
            raise RateLimitError(f"{name} está limitando las peticiones (límite gratuito o sin saldo). "
                                 "Espera un poco y vuelve a intentarlo.") from e
        except openai.BadRequestError as e:
            raise ProviderError(f"{name} rechazó la petición: {e.message}") from e
        except openai.APIStatusError as e:
            raise ProviderError(f"Error de {name} ({e.status_code}). Prueba de nuevo más tarde.") from e
        except openai.APIConnectionError as e:
            if self.local:
                raise ConnectionProblem(f"No se pudo conectar con {name}. ¿Está abierto y con el servidor activado?") from e
            raise ConnectionProblem(f"No se pudo conectar con {name}. Revisa tu conexión a internet.") from e

        if not r.choices:
            raise ProviderError(f"{name} devolvió una respuesta vacía.")
        choice = r.choices[0]
        text = strip_thinking(choice.message.content or "")
        usage = Usage()
        if r.usage:
            usage.input_tokens = r.usage.prompt_tokens or 0
            usage.output_tokens = r.usage.completion_tokens or 0
            details = getattr(r.usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", 0) if details else 0
            usage.cache_read_tokens = cached or 0
            usage.input_tokens -= usage.cache_read_tokens
        return Completion(text=text, usage=usage, model=r.model or self.model,
                          truncated=choice.finish_reason == "length")

    def list_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list())
        except openai.OpenAIError:
            return []
