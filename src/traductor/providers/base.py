"""Interfaz común para todos los proveedores de IA."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Completion:
    text: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    truncated: bool = False  # la respuesta se cortó por límite de longitud


class ProviderError(Exception):
    """Error del proveedor con un mensaje comprensible para el usuario."""


class AuthError(ProviderError):
    pass


class RefusalError(ProviderError):
    pass


class RateLimitError(ProviderError):
    pass


class ConnectionProblem(ProviderError):
    pass


_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


def strip_thinking(text: str) -> str:
    """Quita el razonamiento visible que algunos modelos locales escriben."""
    return _THINK_RE.sub("", text or "").strip()


class Provider(ABC):
    """Un modelo de IA capaz de completar texto."""

    kind: str = ""
    model: str = ""
    #: Modelo local (menos contexto): bloques más pequeños y menos ejemplos.
    local: bool = False
    #: Tokens de salida que se piden por respuesta.
    max_output: int = 16000

    @abstractmethod
    def complete(self, system: str, user: str, *, max_tokens: int | None = None) -> Completion:
        """Envía ``system`` (estable, cacheable) y ``user`` (variable)."""

    def list_models(self) -> list[str]:
        return []

    def describe(self) -> str:
        return f"{self.kind}:{self.model}"
