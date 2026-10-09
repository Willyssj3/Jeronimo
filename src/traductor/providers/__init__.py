"""Creación de proveedores de IA a partir de la configuración del proyecto."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .base import (AuthError, Completion, ConnectionProblem, Provider, ProviderError,
                   RateLimitError, RefusalError, Usage)

KINDS = {
    "anthropic": "Claude (Anthropic) — clave de API",
    "gemini": "Google Gemini — tiene nivel gratuito",
    "openai": "OpenAI — clave de API",
    "openrouter": "OpenRouter — muchos modelos con una sola clave",
    "deepseek": "DeepSeek — clave de API",
    "ollama": "Ollama — local y gratis",
    "lmstudio": "LM Studio — local y gratis",
    "compatible": "Otro servidor compatible con OpenAI",
    "manual": "Modo manual — copiar y pegar en el chat de tu IA",
}

ENV_KEYS = {
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "compatible": ["OPENAI_COMPAT_API_KEY"],
}
KEYRING_SERVICE = "TraductorDeLibros"


def config_dir() -> Path:
    """Carpeta de ajustes del usuario (``%APPDATA%\\TraductorDeLibros`` en Windows)."""
    base = os.environ.get("APPDATA")
    path = Path(base) / "TraductorDeLibros" if base else Path.home() / ".config" / "traductor-de-libros"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _keys_file() -> Path:
    return config_dir() / "claves.json"


def get_api_key(kind: str) -> str | None:
    """Busca la clave en variables de entorno y luego en el almacén seguro del
    sistema (Administrador de credenciales de Windows, Llavero de macOS…)."""
    for var in ENV_KEYS.get(kind, []):
        if os.environ.get(var):
            return os.environ[var]
    try:
        import keyring  # opcional

        key = keyring.get_password(KEYRING_SERVICE, kind)
        if key:
            return key
    except Exception:
        pass
    try:
        return json.loads(_keys_file().read_text(encoding="utf-8")).get(kind)
    except (OSError, ValueError):
        return None


def save_api_key(kind: str, key: str) -> str:
    """Guarda la clave. Devuelve dónde quedó guardada (para informar al usuario)."""
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, kind, key)
        return "almacén seguro del sistema"
    except Exception:
        # Sin almacén seguro (algunos Linux): archivo local legible solo por el usuario.
        path = _keys_file()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[kind] = key
        path.write_text(json.dumps(data), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return f"archivo local {path}"


def make_provider(cfg: dict, api_key: str | None = None) -> Provider:
    """``cfg`` es la sección ``proveedor`` de proyecto.json."""
    kind = cfg.get("tipo") or "anthropic"
    model = cfg.get("modelo") or ""
    if kind == "manual":
        raise ProviderError("Estás en modo manual: usa «Copiar bloque» y «Pegar respuesta» en la revisión del capítulo.")
    if kind == "anthropic":
        from .anthropic_provider import DEFAULT_MODEL, AnthropicProvider

        return AnthropicProvider(api_key or get_api_key(kind), model or DEFAULT_MODEL,
                                 effort=cfg.get("esfuerzo") or "medium")
    if kind == "ollama":
        from .ollama import OllamaProvider

        return OllamaProvider(model, base_url=cfg.get("base_url"),
                              num_ctx=int(cfg.get("num_ctx") or 16384),
                              temperature=float(cfg.get("temperatura") if cfg.get("temperatura") is not None else 0.2))
    if kind in KINDS:
        from .openai_compat import OpenAICompatProvider

        temp = cfg.get("temperatura")
        return OpenAICompatProvider(kind, model, api_key=api_key or get_api_key(kind),
                                    base_url=cfg.get("base_url"),
                                    temperature=float(temp) if temp is not None else None)
    raise ProviderError(f"Proveedor desconocido: {kind}")


__all__ = [
    "AuthError", "Completion", "ConnectionProblem", "KINDS", "Provider", "ProviderError",
    "RateLimitError", "RefusalError", "Usage", "get_api_key", "make_provider", "save_api_key",
]
