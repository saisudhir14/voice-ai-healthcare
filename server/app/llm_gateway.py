"""DaVita LLM API Service (OpenAI-compatible gateway) for Pipecat.

The gateway drops tool_calls when streaming, so each turn is a non-streaming
completion replayed to Pipecat as a short chunk stream.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from typing import Any

import httpx
from loguru import logger
from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import (
    Choice,
    ChoiceDelta,
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)

from pipecat.services.openai.llm import OpenAILLMService

GATEWAY_URL = os.getenv("LLM_SERVICE_URL", "https://llm-service-api-dev.ai-np.davita.com").rstrip("/")
USE_CASE_ID = os.getenv("LLM_USE_CASE_ID", "uid-874fafa0")
MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
FALLBACK_REPLY = "Sorry, I didn't catch that. Could you say it one more time?"


class _IdentityToken:
    """Google ID token for the gateway: metadata server on GCP, gcloud locally."""

    ttl = 300

    def __init__(self) -> None:
        self._token: str | None = None
        self._at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> str:
        async with self._lock:
            if self._token and time.time() - self._at < self.ttl:
                return self._token
            self._token = await asyncio.to_thread(self._fetch)
            self._at = time.time()
            return self._token

    @staticmethod
    def _fetch() -> str:
        if os.getenv("LLM_LOCAL", "true").lower() != "true":
            resp = httpx.get(
                "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity",
                headers={"Metadata-Flavor": "Google"},
                params={"audience": GATEWAY_URL},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.text
        out = subprocess.run(
            ["gcloud", "auth", "print-identity-token"], capture_output=True, text=True, timeout=30
        )
        if out.returncode != 0 or not out.stdout.strip():
            raise RuntimeError(f"gcloud identity token failed: {out.stderr.strip()}")
        return out.stdout.strip()


identity_token = _IdentityToken()


async def gateway_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {await identity_token.get()}",
        "X-Use-Case-Id": USE_CASE_ID,
        "Content-Type": "application/json",
    }


async def complete(messages: list[dict[str, Any]], *, max_tokens: int = 600, temperature: float = 0.2) -> str:
    """One-shot completion (post-call notes, summaries)."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            headers=await gateway_headers(),
            json={
                "model": MODEL,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"].get("content") or ""


class _ReplayStream:
    """Async iterator over prebuilt chunks with the close() hooks Pipecat expects."""

    def __init__(self, chunks: list[ChatCompletionChunk]):
        self._chunks = chunks

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for chunk in self._chunks:
            yield chunk

    async def close(self):
        return None


class _GatewayCompletions:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(45.0))

    async def create(self, **params: Any) -> _ReplayStream:
        params = {k: v for k, v in params.items() if v is not None and not _is_not_given(v)}
        params["stream"] = False
        params.pop("stream_options", None)
        params.pop("service_tier", None)
        params.setdefault("temperature", 0.3)
        params["messages"] = normalize_messages(params.get("messages") or [])
        if not params.get("tools"):
            params.pop("tools", None)
            params.pop("tool_choice", None)
        started = time.perf_counter()
        for attempt in range(3):
            resp = await self._http.post(
                f"{GATEWAY_URL}/v1/chat/completions", headers=await gateway_headers(), json=params
            )
            if resp.status_code >= 400:
                logger.error("LLM gateway {}: {}", resp.status_code, resp.text[:400])
                if attempt < 2 and resp.status_code >= 500:
                    continue
                resp.raise_for_status()
            body = resp.json()
            if not str(body.get("id", "")).startswith("chatcmpl-blocked"):
                break
            logger.warning(
                "LLM gateway safety filter answered '{}' for: {}",
                _text((body.get("choices") or [{}])[0].get("message", {}).get("content"))[:80],
                params["messages"][-1]["content"][:160],
            )
            await asyncio.sleep(0.2)
        else:
            body = {"choices": [{"message": {"role": "assistant", "content": FALLBACK_REPLY}, "finish_reason": "stop"}]}
        logger.info("LLM gateway {} in {:.0f} ms", params.get("model"), (time.perf_counter() - started) * 1000)
        return _ReplayStream(_to_chunks(body))


class _GatewayClient:
    """Duck-types the slice of AsyncOpenAI that Pipecat uses."""

    def __init__(self) -> None:
        self.chat = type("Chat", (), {})()
        self.chat.completions = _GatewayCompletions()

    async def close(self) -> None:
        await self.chat.completions._http.aclose()


class DaVitaGatewayLLMService(OpenAILLMService):
    """OpenAILLMService that talks to the DaVita gateway without streaming."""

    def __init__(self, *, model: str = MODEL, temperature: float = 0.3, max_tokens: int = 220, **kwargs):
        super().__init__(
            api_key="gateway",
            base_url=f"{GATEWAY_URL}/v1",
            settings=OpenAILLMService.Settings(
                model=model, temperature=temperature, max_tokens=max_tokens
            ),
            **kwargs,
        )

        self.asked_this_turn = False
        self.turn_tools: set[str] = set()
        self.busy = False

    def create_client(self, *args, **kwargs):
        return _GatewayClient()

    async def _process_context(self, context):
        self.asked_this_turn = False
        self.turn_tools = set()
        self.busy = True
        try:
            await super()._process_context(context)
        finally:
            self.busy = False

    async def _push_llm_text(self, text: str):
        if "?" in text:
            self.asked_this_turn = True
        await super()._push_llm_text(text)

    async def run_function_calls(self, function_calls):
        self.turn_tools.update(fc.function_name for fc in function_calls)
        await super().run_function_calls(function_calls)


def _text(content: Any) -> str:
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


def normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The gateway rejects role=tool and ignores assistant tool_calls, so fold
    tool traffic into plain text and merge same-role neighbours for Claude."""
    names: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        text = _text(msg.get("content"))
        if role == "developer":
            role = "user"
        if role == "assistant" and msg.get("tool_calls"):
            calls = []
            for call in msg["tool_calls"]:
                fn = call.get("function") or {}
                names[call.get("id", "")] = fn.get("name", "tool")
                calls.append(f"[called {fn.get('name')} {fn.get('arguments') or '{}'}]")
            text = " ".join(filter(None, [text, *calls]))
        elif role == "tool":
            role = "user"
            text = f"[tool result {names.get(msg.get('tool_call_id', ''), 'tool')}] {text}"
        if not text.strip():
            continue
        if out and out[-1]["role"] == role and role != "system":
            out[-1]["content"] += "\n" + text
        else:
            out.append({"role": role, "content": text})
    while len(out) > 1 and out[1]["role"] == "assistant" and out[0]["role"] == "system":
        out.insert(1, {"role": "user", "content": "[call connected]"})
    return out


def _is_not_given(value: Any) -> bool:
    return type(value).__name__ in {"NotGiven", "_NotGiven", "NotGivenType", "Omit"}


def _to_chunks(body: dict[str, Any]) -> list[ChatCompletionChunk]:
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    model = body.get("model") or MODEL
    cid = body.get("id") or "gateway"
    created = int(body.get("created") or time.time())

    def chunk(delta: ChoiceDelta, finish: str | None = None) -> ChatCompletionChunk:
        return ChatCompletionChunk(
            id=cid,
            object="chat.completion.chunk",
            created=created,
            model=model,
            choices=[Choice(index=0, delta=delta, finish_reason=finish)],
        )

    chunks: list[ChatCompletionChunk] = []
    if message.get("content"):
        chunks.append(chunk(ChoiceDelta(role="assistant", content=message["content"])))
    for index, call in enumerate(message.get("tool_calls") or []):
        fn = call.get("function") or {}
        chunks.append(
            chunk(
                ChoiceDelta(
                    tool_calls=[
                        ChoiceDeltaToolCall(
                            index=index,
                            id=call.get("id"),
                            type="function",
                            function=ChoiceDeltaToolCallFunction(
                                name=fn.get("name"), arguments=fn.get("arguments") or "{}"
                            ),
                        )
                    ]
                )
            )
        )
    chunks.append(chunk(ChoiceDelta(), choice.get("finish_reason") or "stop"))
    return chunks
