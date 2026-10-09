"""Modelos locales gratuitos con Ollama (https://ollama.com).

Se usa la API nativa de Ollama (no la compatible con OpenAI) porque permite
fijar el tamaño de contexto (``num_ctx``); con el valor por defecto, Ollama
recortaría en silencio las instrucciones largas."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .base import Completion, ConnectionProblem, Provider, ProviderError, Usage, strip_thinking

DEFAULT_URL = "http://localhost:11434"


class OllamaProvider(Provider):
    kind = "ollama"
    local = True

    def __init__(self, model: str, *, base_url: str | None = None, num_ctx: int = 16384,
                 temperature: float = 0.2, timeout: float = 1800.0):
        if not model:
            raise ProviderError("Elige un modelo de Ollama (por ejemplo, uno que hayas descargado con «ollama pull»).")
        self.model = model
        self.base_url = (base_url or DEFAULT_URL).rstrip("/")
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.timeout = timeout
        self.max_output = 4096
        # Ollama corre en el propio equipo: sin proxies.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request(self, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            if e.code == 404:
                raise ProviderError(f"Ollama no tiene el modelo «{self.model}». "
                                    f"Descárgalo con: ollama pull {self.model}") from e
            raise ProviderError(f"Ollama devolvió un error ({e.code}): {detail[:300]}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise ConnectionProblem("No se pudo conectar con Ollama. ¿Está instalado y abierto?") from e

    def complete(self, system: str, user: str, *, max_tokens: int | None = None) -> Completion:
        data = self._request("/api/chat", {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {
                "num_ctx": self.num_ctx,
                "temperature": self.temperature,
                "num_predict": max_tokens or self.max_output,
            },
        })
        text = strip_thinking((data.get("message") or {}).get("content", ""))
        usage = Usage(input_tokens=data.get("prompt_eval_count", 0) or 0,
                      output_tokens=data.get("eval_count", 0) or 0)
        return Completion(text=text, usage=usage, model=self.model,
                          truncated=data.get("done_reason") == "length")

    def list_models(self) -> list[str]:
        try:
            data = self._request("/api/tags")
        except ProviderError:
            return []
        return sorted(m.get("name", "") for m in data.get("models", []) if m.get("name"))
