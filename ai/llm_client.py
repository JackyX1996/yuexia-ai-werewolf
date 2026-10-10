import json
import asyncio
import re
import time
from typing import Dict, Union
import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from config import settings
from ai.model_pool import extra_body_for, max_tokens_for, supports_json_mode, temperature_for, streams_reasoning_for
from ai.model_configuration import ModelConnection, resolve_connection
from ai.diagnostics import LLMResponseError, update_call_metrics


_THINK_RE = re.compile(
    r"<(?:think|mm:think)>.*?</(?:think|mm:think)>",
    re.DOTALL | re.IGNORECASE,
)


def _message_text(message) -> str:
    """Final answer text. Thinking-only models sometimes leave content empty."""
    content = getattr(message, "content", None) or ""
    if not str(content).strip():
        content = getattr(message, "reasoning_content", None) or ""
    content = _THINK_RE.sub("", str(content)).strip()
    if not content:
        raise LLMResponseError("empty_response", "model returned empty content")
    return content


def _extract_json(text: str) -> dict:
    """Extract JSON from text, handling markdown code blocks."""
    text = text.strip()
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from ```json ... ``` blocks
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
    if m:
        return json.loads(m.group(1).strip())
    # Try finding first { ... } block
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise json.JSONDecodeError("No JSON found in response", text, 0)


def request_url(base_url: str, api_format: str) -> str:
    """Root URL plus the path for Responses or Anthropic Messages."""
    root = (base_url or "").rstrip("/")
    if api_format == "responses":
        return root + "/responses"
    if api_format == "anthropic_messages":
        return (root if root.endswith("/v1") else root + "/v1") + "/messages"
    return root + "/chat/completions"


class LLMClient:
    def __init__(self, model_name: str = None, connection: ModelConnection = None):
        self.connection = connection or resolve_connection(model_name)
        self.model = self.connection.model_name
        self.api_format = getattr(self.connection, "api_format", None) or "chat_completions"
        self.extra_body = extra_body_for(self.model, self.connection.provider,
                                         self.connection.thinking_enabled, self.connection.thinking_format)
        self._http = None
        self.client = None
        if self.api_format == "chat_completions":
            self.client = AsyncOpenAI(
                api_key=self.connection.api_key,
                base_url=self.connection.base_url,
                timeout=settings.llm_timeout_seconds,
                max_retries=0,  # ai_decide owns the bounded retry policy.
            )
        self.supports_json_mode = supports_json_mode(self.model)

    async def aclose(self):
        if self.client is not None:
            await self.client.close()
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def chat(self, system_prompt: str, user_prompt: str,
                   temperature: float = None, json_mode: bool = True) -> Union[dict, str]:
        """Send a chat request and return parsed response."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return await self._chat(messages, temperature, json_mode)

    def _sampling_temperature(self, temperature: float):
        value = temperature_for(self.model, settings.llm_temperature if temperature is None else temperature)
        if self.connection.provider == "openai" and re.match(r"^(gpt-[56]|o[134](?:-|$))", self.model.lower()):
            return None
        return value

    async def _chat(self, messages: list, temperature: float, json_mode: bool) -> Union[dict, str]:
        api_format = getattr(self, "api_format", None) or "chat_completions"
        if api_format == "responses":
            content = await self._responses(messages, temperature, json_mode)
        elif api_format == "anthropic_messages":
            content = await self._anthropic(messages, temperature)
        else:
            content = await self._chat_completions(messages, temperature, json_mode)
        if json_mode:
            return _extract_json(content)
        return content

    async def _chat_completions(self, messages: list, temperature: float, json_mode: bool) -> str:
        # One call-shape policy for both single and multi-turn requests.
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self._sampling_temperature(temperature),
            "max_tokens": self._max_tokens(),
        }
        if kwargs["temperature"] is None:
            # OpenAI reasoning models use completion-token budgets and may reject
            # temperature. This also applies when the server is a compatible proxy.
            kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
            kwargs.pop("temperature")
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        if json_mode and self.supports_json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        if streams_reasoning_for(self.model):
            # Also bound direct callers (e.g. memory compression). ai_decide's
            # outer deadline still shares a smaller remaining budget on retry.
            content = await asyncio.wait_for(self._stream_answer(kwargs),
                                            timeout=settings.llm_decision_timeout_seconds)
        else:
            update_call_metrics(transport="nonstream")
            response = await self.client.chat.completions.create(**kwargs)
            if not response.choices:
                raise LLMResponseError("empty_response", "model returned no choices")
            finish = response.choices[0].finish_reason
            update_call_metrics(finish_reason=finish)
            if finish == "length":
                raise LLMResponseError("truncated", "model response was truncated; no completed decision")
            content = _message_text(response.choices[0].message)
        return content

    async def _responses(self, messages: list, temperature: float, json_mode: bool) -> str:
        body = {
            "model": self.model,
            "input": messages,
            "max_output_tokens": self._max_tokens(),
        }
        temperature = self._sampling_temperature(temperature)
        if temperature is not None:
            body["temperature"] = temperature
        if self.extra_body.get("reasoning_effort"):
            body["reasoning"] = {"effort": self.extra_body["reasoning_effort"]}
        if json_mode and self.supports_json_mode:
            body["text"] = {"format": {"type": "json_object"}}
        data = await self._post_json(request_url(self.connection.base_url, "responses"), {
            "Authorization": "Bearer " + self.connection.api_key,
        }, body)
        if data.get("status") not in (None, "completed"):
            reason = (data.get("incomplete_details") or {}).get("reason")
            if reason == "max_output_tokens":
                raise LLMResponseError("truncated", "model response was truncated; no completed decision")
            raise LLMResponseError("incomplete_stream", "response ended without a completed answer")
        text = data.get("output_text") or ""
        if not text:
            for item in data.get("output") or []:
                if not isinstance(item, dict):
                    continue
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                        text += part.get("text") or ""
        content = _THINK_RE.sub("", text).strip()
        if not content:
            raise LLMResponseError("empty_response", "model returned empty content")
        return content

    async def _anthropic(self, messages: list, temperature: float) -> str:
        system = "\n\n".join(item["content"] for item in messages if item.get("role") == "system" and item.get("content"))
        dialog = [item for item in messages if item.get("role") != "system"]
        body = {
            "model": self.model,
            "max_tokens": self._max_tokens(),
            "messages": dialog or [{"role": "user", "content": system or "请回复。"}],
        }
        if system and dialog:
            body["system"] = system
        temperature = self._sampling_temperature(temperature)
        if temperature is not None:
            body["temperature"] = temperature
        data = await self._post_json(request_url(self.connection.base_url, "anthropic_messages"), {
            "x-api-key": self.connection.api_key,
            "anthropic-version": "2023-06-01",
            "Authorization": "Bearer " + self.connection.api_key,
        }, body)
        if data.get("stop_reason") == "max_tokens":
            raise LLMResponseError("truncated", "model response was truncated; no completed decision")
        text = "".join(part.get("text") or "" for part in data.get("content") or []
                       if isinstance(part, dict) and part.get("type") == "text")
        content = _THINK_RE.sub("", text).strip()
        if not content:
            raise LLMResponseError("empty_response", "model returned empty content")
        return content

    async def _post_json(self, url: str, headers: dict, body: dict) -> dict:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=settings.llm_timeout_seconds)
        try:
            response = await self._http.post(url, headers=dict(headers, **{"Content-Type": "application/json"}), json=body)
        except httpx.TimeoutException as error:
            request = getattr(error, "request", None) or httpx.Request("POST", url)
            raise APITimeoutError(request) from error
        except httpx.RequestError as error:
            request = getattr(error, "request", None) or httpx.Request("POST", url)
            raise APIConnectionError(message="connection error", request=request) from error
        if response.status_code >= 400:
            raise APIStatusError("HTTP {0}".format(response.status_code), response=response, body=None)
        try:
            return response.json()
        except json.JSONDecodeError as error:
            raise LLMResponseError("invalid_json", "model returned a non-JSON payload") from error

    async def _stream_answer(self, kwargs: dict) -> str:
        """Consume private reasoning as liveness, adopt only a completed answer.

        ai_decide bounds total wall time across retries. SDK read timeout now
        means no data for 45s, rather than no completed answer for 45s.
        """
        started = time.monotonic()
        update_call_metrics(transport="stream", chunks=0, reasoning_chars=0, content_chars=0)
        stream = await self.client.chat.completions.create(**dict(kwargs, stream=True))
        pieces = []
        finish = None
        chunks = reasoning_chars = content_chars = 0
        first_chunk = first_answer = None
        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                chunks += 1
                reasoning = getattr(delta, "reasoning_content", None) or ""
                text = getattr(delta, "content", None) or ""
                if (reasoning or text) and first_chunk is None:
                    first_chunk = round(time.monotonic() - started, 3)
                # Never use or persist reasoning_content as the final decision.
                reasoning_chars += len(reasoning)
                if text:
                    if first_answer is None:
                        first_answer = round(time.monotonic() - started, 3)
                    pieces.append(text)
                    content_chars += len(text)
                if choice.finish_reason is not None:
                    finish = choice.finish_reason
                update_call_metrics(chunks=chunks, reasoning_chars=reasoning_chars,
                                    content_chars=content_chars, first_chunk_seconds=first_chunk,
                                    first_answer_seconds=first_answer, finish_reason=finish)
                # A finished answer is handled below. A reasoning-only stream
                # that never starts one would otherwise consume the whole budget.
                if (content_chars == 0 and settings.llm_reasoning_only_seconds > 0
                        and time.monotonic() - started >= settings.llm_reasoning_only_seconds):
                    raise LLMResponseError(
                        "reasoning_overrun",
                        "answer did not start before the reasoning budget",
                    )
        finally:
            await stream.close()
        if finish == "length":
            raise LLMResponseError("truncated", "model response was truncated; no completed decision")
        if finish != "stop":
            raise LLMResponseError("incomplete_stream", "stream ended without a completed answer")
        content = _THINK_RE.sub("", "".join(pieces)).strip()
        if not content:
            raise LLMResponseError("empty_response", "model returned no final answer")
        return content

    def _max_tokens(self) -> int:
        return max_tokens_for(self.model, settings.llm_max_tokens)

    async def chat_multi_turn(self, system_prompt: str, messages: list,
                              temperature: float = None, json_mode: bool = True) -> Union[dict, str]:
        """Multi-turn conversation (for wolf discussion)."""
        all_messages = [{"role": "system", "content": system_prompt}] + messages
        return await self._chat(all_messages, temperature, json_mode)


# Client cache: configuration ID → LLMClient. Changes rebuild the connection.
_client_cache: Dict[str, LLMClient] = {}


def get_llm_client(model_name: str = None) -> LLMClient:
    """Factory function with cache. Returns LLMClient for the given model."""
    connection = resolve_connection(model_name)
    key = connection.id
    if key not in _client_cache or _client_cache[key].connection != connection:
        _client_cache[key] = LLMClient(connection=connection)
    return _client_cache[key]
