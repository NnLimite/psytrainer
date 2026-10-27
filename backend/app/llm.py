"""Клиент LLM. Сервисы зависят только от протокола LLMClient: Ollama можно заменить на LiteLLM или облачную модель."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

import httpx


@dataclass
class LLMUsage:
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


class LLMClient(Protocol):
    model: str

    async def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.8,
                   json_mode: bool = False) -> tuple[str, LLMUsage]: ...

    def chat_stream(self, messages: list[dict[str, str]], *, temperature: float = 0.8) -> AsyncIterator[str | LLMUsage]:
        """Отдаёт строки-токены, а в конце один объект LLMUsage."""
        ...

    async def ping(self) -> bool: ...


class OllamaClient:
    def __init__(self, base_url: str, model: str, *, timeout: float = 180.0, num_ctx: int = 8192,
                 think: bool | None = False) -> None:
        self.model, self.num_ctx, self.think = model, num_ctx, think
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    def _payload(self, messages, temperature, stream, json_mode) -> dict:
        p: dict = {"model": self.model, "messages": messages, "stream": stream,
                   "options": {"temperature": temperature, "num_ctx": self.num_ctx}}
        if json_mode:
            p["format"] = "json"
        if self.think is not None:
            p["think"] = self.think  # для Qwen3: выключает «рассуждения» в ответе
        return p

    async def chat(self, messages, *, temperature: float = 0.8, json_mode: bool = False):
        t0 = time.monotonic()
        r = await self._client.post("/api/chat", json=self._payload(messages, temperature, False, json_mode))
        r.raise_for_status()
        data = r.json()
        usage = LLMUsage(self.model, data.get("prompt_eval_count", 0), data.get("eval_count", 0),
                         int((time.monotonic() - t0) * 1000))
        return data["message"]["content"], usage

    async def chat_stream(self, messages, *, temperature: float = 0.8):
        t0 = time.monotonic()
        usage = LLMUsage(self.model)
        async with self._client.stream("POST", "/api/chat", json=self._payload(messages, temperature, True, False)) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if data.get("done"):
                    usage.tokens_in = data.get("prompt_eval_count", 0)
                    usage.tokens_out = data.get("eval_count", 0)
                    break
                chunk = data.get("message", {}).get("content", "")
                if chunk:
                    yield chunk
        usage.latency_ms = int((time.monotonic() - t0) * 1000)
        yield usage

    async def ping(self) -> bool:
        try:
            r = await self._client.get("/api/tags", timeout=3.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
