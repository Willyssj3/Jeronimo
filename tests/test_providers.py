"""Pruebas de los adaptadores contra un servidor HTTP local que imita las APIs
(sin gastar saldo ni necesitar claves reales)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from traductor.providers import AuthError, RefusalError, make_provider
from traductor.providers.anthropic_provider import AnthropicProvider, estimate_cost
from traductor.providers.base import Usage


def _sse(stop_reason="end_turn", text='<seg id="0">Hola</seg>'):
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 5,
                      "cache_creation_input_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                           "usage": {"output_tokens": 7}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


class Handler(BaseHTTPRequestHandler):
    log = []
    mode = {"anthropic_stop": "end_turn"}

    def log_message(self, *args):
        pass

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        Handler.log.append(("GET", self.path, dict(self.headers), None))
        if self.path == "/api/tags":
            self._send(200, json.dumps({"models": [{"name": "gemma3:12b"}]}).encode())
        else:
            self._send(404, b"{}")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.log.append(("POST", self.path, dict(self.headers), body))
        if self.path.startswith("/v1/messages"):
            if self.headers.get("x-api-key") == "mala":
                err = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
                self._send(401, json.dumps(err).encode())
                return
            self._send(200, _sse(Handler.mode["anthropic_stop"]), "text/event-stream")
        elif self.path == "/v1/chat/completions":
            self._send(200, json.dumps({
                "id": "c1", "object": "chat.completion", "created": 0, "model": body["model"],
                "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": "<think>pensando…</think>\n<seg id=\"0\">Hola</seg>"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                          "prompt_tokens_details": {"cached_tokens": 4}},
            }).encode())
        elif self.path == "/api/chat":
            self._send(200, json.dumps({
                "message": {"role": "assistant", "content": '<seg id="0">Hola</seg>'},
                "done": True, "done_reason": "stop", "prompt_eval_count": 12, "eval_count": 3,
            }).encode())
        else:
            self._send(404, b"{}")


@pytest.fixture
def server():
    Handler.log.clear()
    Handler.mode["anthropic_stop"] = "end_turn"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _anthropic(base, key="buena", model="claude-opus-5-5"):
    p = AnthropicProvider(key, model, max_retries=0)
    p.client = p.client.with_options(base_url=base)
    return p


def test_anthropic_request_and_response(server):
    result = _anthropic(server).complete("instrucciones", "texto")
    assert result.text == '<seg id="0">Hola</seg>'
    assert result.usage.cache_read_tokens == 5 and result.usage.output_tokens == 7
    _method, path, headers, body = Handler.log[-1]
    assert path.startswith("/v1/messages")
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in headers.get("anthropic-beta", "")
    assert body["output_config"] == {"effort": "medium"}
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["stream"] is True


def test_anthropic_haiku_has_no_fallbacks(server):
    _anthropic(server, model="claude-haiku-5-5").complete("s", "u")
    _method, _path, headers, body = Handler.log[-1]
    assert "fallbacks" not in body and "anthropic-beta" not in {k.lower() for k in headers}


def test_anthropic_refusal_and_auth(server):
    Handler.mode["anthropic_stop"] = "refusal"
    with pytest.raises(RefusalError):
        _anthropic(server).complete("s", "u")
    with pytest.raises(AuthError):
        _anthropic(server, key="mala").complete("s", "u")


def test_anthropic_needs_key():
    with pytest.raises(AuthError):
        AnthropicProvider(None)


def test_openai_compatible(server):
    p = make_provider({"tipo": "compatible", "modelo": "mi-modelo", "base_url": server + "/v1"}, api_key="k")
    result = p.complete("s", "u")
    assert result.text == '<seg id="0">Hola</seg>'  # el razonamiento <think> se elimina
    assert result.usage.cache_read_tokens == 4 and result.usage.input_tokens == 6
    body = Handler.log[-1][3]
    # Un servidor en el propio equipo se trata como modelo local.
    assert p.local and body["max_tokens"] == 4096 and body["temperature"] == 0.2
    assert body["messages"][0]["role"] == "system"


def test_gemini_requires_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr("traductor.providers.get_api_key", lambda kind: None)
    with pytest.raises(AuthError):
        make_provider({"tipo": "gemini", "modelo": "gemini-x"})


def test_ollama(server):
    p = make_provider({"tipo": "ollama", "modelo": "gemma3:12b", "base_url": server})
    assert p.local
    result = p.complete("s", "u")
    assert result.text == '<seg id="0">Hola</seg>' and result.usage.input_tokens == 12
    body = Handler.log[-1][3]
    assert body["options"]["num_ctx"] == 16384 and body["stream"] is False
    assert p.list_models() == ["gemma3:12b"]


def test_cost_estimate():
    cost = estimate_cost("claude-opus-5-5", Usage(input_tokens=1_000_000, output_tokens=100_000))
    assert cost == pytest.approx(4.0 + 2.0)
    assert estimate_cost("modelo-desconocido", Usage()) is None
