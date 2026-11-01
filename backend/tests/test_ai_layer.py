"""The AI client against a fake Groq-compatible server (standard library
only): retired models, rate limits, streaming, keys, transcription."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import llm_client as L

STATE = {}


class FakeGroq(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj, headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            if self.headers.get("Authorization") != "Bearer good-key":
                return self._json(401, {"error": {"message": "Invalid API Key", "code": "invalid_api_key"}})
            return self._json(200, {"data": [{"id": m} for m in STATE.get("models", [])]})
        self._json(404, {})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        if self.headers.get("Authorization") != "Bearer good-key":
            return self._json(401, {"error": {"message": "Invalid API Key", "code": "invalid_api_key"}})
        if self.path.endswith("/audio/transcriptions"):
            STATE["audio_bytes"] = len(raw)
            return self._json(200, {"text": "Rent is twenty thousand rupees"})
        body = json.loads(raw)
        STATE.setdefault("calls", []).append(body["model"])
        model = body["model"]
        if model in STATE.get("retired", ()):
            return self._json(400, {"error": {"message": f"The model `{model}` has been decommissioned", "code": "model_decommissioned"}})
        if STATE.get("rate_limit_main") and model == "main-model":
            return self._json(429, {"error": {"message": "Rate limit reached"}}, {"retry-after": "0"})
        if body.get("stream"):
            if STATE.get("no_stream"):
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
                self.wfile.write(b"data: [DONE]\n\n")
                return
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            for piece in ["Hel", "lo ", "there"]:
                self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': piece}}]})}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        return self._json(200, {"choices": [{"message": {"content": f"answer from {model}"}}]})


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeGroq)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/openai/v1"
    srv.shutdown()


@pytest.fixture
def groq(server, monkeypatch):
    STATE.clear()
    STATE["models"] = ["llama-3.3-70b-versatile", "main-model", "small-model", "whisper-large-v3-turbo"]
    monkeypatch.setenv("GROQ_API_KEY", "good-key")
    monkeypatch.setenv("LLM_API_URL", server + "/chat/completions")
    monkeypatch.setenv("LLM_MODEL", "main-model")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "small-model")
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    L._resolved.clear()
    return server


def test_plain_answer(groq):
    assert L.chat_completion([{"role": "user", "content": "hi"}]) == "answer from main-model"


def test_retired_model_switches_automatically(groq):
    STATE["retired"] = {"main-model"}
    assert L.chat_completion([{"role": "user", "content": "hi"}]) == "answer from llama-3.3-70b-versatile"
    # remembered for the next call
    STATE["calls"].clear()
    L.chat_completion([{"role": "user", "content": "hi"}])
    assert STATE["calls"] == ["llama-3.3-70b-versatile"]


def test_rate_limit_uses_fallback_model(groq):
    STATE["rate_limit_main"] = True
    assert L.chat_completion([{"role": "user", "content": "hi"}]) == "answer from small-model"


def test_streaming_and_empty_stream_fallback(groq):
    assert "".join(L.stream_chat([{"role": "user", "content": "hi"}])) == "Hello there"
    STATE["no_stream"] = True   # e.g. a proxy that buffers event streams away
    assert "".join(L.stream_chat([{"role": "user", "content": "hi"}])) == "answer from main-model"


def test_bad_key_gives_clear_message(groq, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "wrong")
    with pytest.raises(L.LLMUnavailable) as e:
        L.chat_completion([{"role": "user", "content": "hi"}])
    assert "rejected the API key" in str(e.value)


def test_unreachable_server_gives_clear_message(groq, monkeypatch):
    monkeypatch.setenv("LLM_API_URL", "http://127.0.0.1:9/openai/v1/chat/completions")
    with pytest.raises(L.LLMUnavailable) as e:
        L.chat_completion([{"role": "user", "content": "hi"}])
    assert "Can't reach" in str(e.value)


def test_transcription(groq):
    assert L.transcribe(b"x" * 1000, "a.webm", "en") == "Rent is twenty thousand rupees"
    assert STATE["audio_bytes"] > 1000
