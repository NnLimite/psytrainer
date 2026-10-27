"""OllamaClient против имитации HTTP-протокола Ollama (без реальной модели)."""
from __future__ import annotations

import json

import httpx

from app.llm import LLMUsage, OllamaClient


def make_client(handler) -> OllamaClient:
    c = OllamaClient("http://ollama", "qwen3:30b", num_ctx=4096)
    c._client = httpx.AsyncClient(base_url="http://ollama", transport=httpx.MockTransport(handler))
    return c


async def test_chat_sends_expected_payload_and_returns_usage():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": '{"a": 1}'}, "prompt_eval_count": 42, "eval_count": 7})

    text, usage = await make_client(handler).chat([{"role": "user", "content": "hi"}], temperature=0.0, json_mode=True)
    assert text == '{"a": 1}' and (usage.tokens_in, usage.tokens_out, usage.model) == (42, 7, "qwen3:30b")
    assert seen["model"] == "qwen3:30b" and seen["stream"] is False and seen["format"] == "json"
    assert seen["think"] is False and seen["options"] == {"temperature": 0.0, "num_ctx": 4096}


async def test_stream_parses_ndjson_and_ends_with_usage():
    lines = [{"message": {"content": "Ну"}, "done": False}, {"message": {"content": " ладно"}, "done": False},
             {"message": {"content": ""}, "done": True, "prompt_eval_count": 100, "eval_count": 3}]
    body = "\n".join(json.dumps(x) for x in lines) + "\n"

    def handler(req: httpx.Request) -> httpx.Response:
        assert json.loads(req.content)["stream"] is True
        return httpx.Response(200, content=body.encode())

    items = [i async for i in make_client(handler).chat_stream([{"role": "user", "content": "hi"}])]
    assert items[:2] == ["Ну", " ладно"] and isinstance(items[2], LLMUsage)
    assert (items[2].tokens_in, items[2].tokens_out) == (100, 3)


async def test_http_error_and_ping():
    c = make_client(lambda req: httpx.Response(500))
    try:
        await c.chat([{"role": "user", "content": "x"}])
        raise AssertionError("ожидали HTTPStatusError")
    except httpx.HTTPStatusError:
        pass
    assert await c.ping() is False
    assert await make_client(lambda req: httpx.Response(200, json={"models": []})).ping() is True
