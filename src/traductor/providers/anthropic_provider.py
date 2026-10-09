"""Claude (Anthropic) mediante el SDK oficial.

Usa una clave de API de la consola de Anthropic. La suscripción de claude.ai
(Pro/Max) no sirve para apps de terceros."""

from __future__ import annotations

import anthropic

from .base import (AuthError, Completion, ConnectionProblem, Provider, ProviderError,
                   RateLimitError, RefusalError, Usage)

DEFAULT_MODEL = "claude-opus-5-5"

# Modelos con reintento automático en otro modelo si un filtro de seguridad
# rechaza la petición (parámetro ``fallbacks: "default"``).
FALLBACK_MODELS = {"claude-fable-5-1", "claude-fable-5", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
# Modelos antiguos que no aceptan ``output_config.effort``.
NO_EFFORT_PREFIXES = ("claude-haiku-4-5", "claude-sonnet-4-5", "claude-3", "claude-opus-4-1",
                      "claude-opus-4-0", "claude-sonnet-4-0", "claude-opus-4-2025", "claude-sonnet-4-2025")

# Precios en USD por millón de tokens: entrada, salida, lectura de caché, escritura de caché.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5),
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-opus-5": (5.0, 25.0, 0.50, 6.25),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}

SUGGESTED_MODELS = [
    ("claude-opus-5-5", "Opus 5.5 — máxima calidad recomendada"),
    ("claude-sonnet-5-5", "Sonnet 5.5 — muy buena calidad, la mitad de precio"),
    ("claude-haiku-5-5", "Haiku 5.5 — económico, para borradores"),
]


def estimate_cost(model: str, usage: Usage) -> float | None:
    price = PRICES.get(model)
    if not price:
        return None
    p_in, p_out, p_read, p_write = price
    return (usage.input_tokens * p_in + usage.output_tokens * p_out
            + usage.cache_read_tokens * p_read + usage.cache_write_tokens * p_write) / 1_000_000


class AnthropicProvider(Provider):
    kind = "anthropic"

    def __init__(self, api_key: str | None, model: str = DEFAULT_MODEL, *, effort: str = "medium",
                 max_retries: int = 4, timeout: float = 900.0):
        if not api_key:
            raise AuthError(
                "Falta la clave de API de Anthropic. Créala en console.anthropic.com "
                "(se paga por uso, aparte de la suscripción de claude.ai).")
        self.model = model or DEFAULT_MODEL
        self.effort = effort
        self.max_output = 32000
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=max_retries, timeout=timeout)

    def _params(self, system: str, user: str, max_tokens: int) -> dict:
        params: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            # Las instrucciones y la guía de estilo no cambian entre bloques:
            # se cachean y cada bloque siguiente cuesta mucho menos.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }
        if self.effort and not self.model.startswith(NO_EFFORT_PREFIXES):
            params["output_config"] = {"effort": self.effort}
        if self.model in FALLBACK_MODELS:
            params["betas"] = ["server-side-fallback-2026-07-01"]
            params["fallbacks"] = "default"
        return params

    def complete(self, system: str, user: str, *, max_tokens: int | None = None) -> Completion:
        params = self._params(system, user, max_tokens or self.max_output)
        try:
            with self.client.beta.messages.stream(**params) as stream:
                msg = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise AuthError("La clave de API de Anthropic no es válida.") from e
        except anthropic.PermissionDeniedError as e:
            raise AuthError(f"Tu clave de Anthropic no tiene permiso para usar «{self.model}».") from e
        except anthropic.NotFoundError as e:
            raise ProviderError(f"El modelo «{self.model}» no existe o tu cuenta no tiene acceso.") from e
        except anthropic.RateLimitError as e:
            raise RateLimitError("Anthropic está limitando las peticiones (demasiadas o sin saldo). "
                                 "Espera un poco o revisa tu saldo en console.anthropic.com.") from e
        except anthropic.BadRequestError as e:
            raise ProviderError(f"Anthropic rechazó la petición: {e.message}") from e
        except anthropic.APIStatusError as e:
            raise ProviderError(f"Error del servidor de Anthropic ({e.status_code}). Prueba de nuevo más tarde.") from e
        except anthropic.APIConnectionError as e:
            raise ConnectionProblem("No se pudo conectar con Anthropic. Revisa tu conexión a internet.") from e

        if msg.stop_reason == "refusal":
            raise RefusalError("Claude no quiso traducir este fragmento (filtro de seguridad). "
                               "Puedes traducirlo a mano o probar con otro modelo.")
        text = "".join(block.text for block in msg.content if block.type == "text")
        u = msg.usage
        usage = Usage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        return Completion(text=text, usage=usage, model=msg.model, truncated=msg.stop_reason == "max_tokens")

    def list_models(self) -> list[str]:
        try:
            return [m.id for m in self.client.models.list()]
        except anthropic.APIError:
            return [m for m, _label in SUGGESTED_MODELS]
