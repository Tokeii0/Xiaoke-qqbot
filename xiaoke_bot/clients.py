from __future__ import annotations

import base64
import json
import re
from collections import OrderedDict
from dataclasses import replace
from typing import Any

import httpx
from nonebot import logger

from .config import Settings
from .request_log import call_scope, trace_request


class ServiceError(RuntimeError):
    pass


_THINK_BLOCK = re.compile(
    r"[ \t]*<think(?:\s[^>]*)?>.*?</think\s*>[ \t]*(?:\r?\n)?",
    flags=re.IGNORECASE | re.DOTALL,
)
_THINK_PREFIX_WITHOUT_OPEN = re.compile(
    r"^.*?</think\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_THINK_SUFFIX_WITHOUT_CLOSE = re.compile(
    r"<think(?:\s[^>]*)?>.*$",
    flags=re.IGNORECASE | re.DOTALL,
)


def strip_think_blocks(content: str) -> str:
    """Remove model reasoning tags before display, history, and memory capture."""
    text = _THINK_BLOCK.sub("", content)
    # Some compatible providers truncate one side of the tag. A leading
    # orphan closing tag means everything before it was hidden reasoning;
    # an unclosed opening tag means everything after it is unsafe to expose.
    text = _THINK_PREFIX_WITHOUT_OPEN.sub("", text, count=1)
    text = _THINK_SUFFIX_WITHOUT_CLOSE.sub("", text, count=1)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error)
            if error:
                return str(error)
    except ValueError:
        pass
    return response.text[:500] or f"HTTP {response.status_code}"


class ChatClient:
    def __init__(self, request_log=None) -> None:
        self.request_log = request_log
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0))

    async def close(self) -> None:
        await self.http.aclose()

    @staticmethod
    def _fallback_settings(settings: Settings) -> Settings | None:
        if not settings.fallback_configured:
            return None
        return replace(
            settings,
            api_base_url=settings.fallback_base_url,
            api_key=settings.fallback_key,
            model=settings.fallback_model,
        )

    async def complete(self, messages: list[dict[str, Any]], settings: Settings, *, feature=None) -> str:
        with call_scope(redact_secrets=(settings.api_key, settings.fallback_api_key)):
            return await self._complete_with_fallback(messages, settings, feature=feature)

    async def _complete_with_fallback(self, messages, settings, *, feature=None):
        try:
            return await self._complete_once(messages, settings, feature=feature)
        except ServiceError as primary_error:
            fallback = self._fallback_settings(settings)
            if fallback is None:
                raise
            logger.warning(f"主对话模型失败，改用备用模型 {fallback.model}：{primary_error}")
            try:
                return await self._complete_once(messages, fallback, feature=feature)
            except ServiceError as fallback_error:
                raise ServiceError(
                    f"主模型与备用模型均失败：主={primary_error}；备={fallback_error}"
                ) from fallback_error

    async def _complete_once(self, messages: list[dict[str, Any]], settings: Settings, *, feature=None) -> str:
        if not settings.chat_configured:
            raise ServiceError("尚未配置 BOT_API_KEY")
        payload: dict[str, Any] = {
            "model": settings.model,
            "messages": messages,
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
            "top_p": settings.top_p,
            "presence_penalty": settings.presence_penalty,
            "frequency_penalty": settings.frequency_penalty,
        }
        if settings.seed is not None:
            payload["seed"] = settings.seed
        if settings.reasoning_effort:
            payload["reasoning_effort"] = settings.reasoning_effort
        if settings.response_format != "text":
            payload["response_format"] = {"type": settings.response_format}
        if settings.stop_sequences:
            payload["stop"] = list(settings.stop_sequences)
        extra_body = json.loads(settings.extra_body_json or "{}")
        payload.update(extra_body)

        async with trace_request(self.request_log, kind="chat", settings=settings, request=payload,
                model=settings.model, endpoint=settings.api_base_url + "/chat/completions", feature=feature) as trace:
            return await self._send_completion(payload, settings, trace)

    async def _send_completion(self, payload, settings, trace):

        try:
            response = await self.http.post(
                f"{settings.api_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=httpx.Timeout(
                    settings.request_timeout, connect=min(10.0, settings.request_timeout)
                ),
            )
        except httpx.HTTPError as exc:
            raise ServiceError(f"对话模型连接失败：{exc}") from exc
        if response.is_error:
            raise ServiceError(f"对话模型请求失败：{_error_text(response)}")
        data: dict[str, Any] = response.json()
        trace.usage = data.get("usage")
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ServiceError("对话模型返回格式不正确") from exc
        if isinstance(content, list):
            content = "".join(
                str(item.get("text", "")) for item in content if isinstance(item, dict)
            )
        text = strip_think_blocks(str(content or ""))
        if not text:
            raise ServiceError("对话模型清除思考内容后没有可显示的回答")
        trace.response = {"text": text, "finish_reason": data["choices"][0].get("finish_reason")}
        return text


class VisionClient:
    """Image transport for direct chat, plus the optional legacy description route."""

    MAX_IMAGE_BYTES = 6 * 1024 * 1024
    MAX_CACHE_BYTES = 24 * 1024 * 1024

    def __init__(self, request_log=None) -> None:
        self.request_log = request_log
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
        self._image_cache = OrderedDict()
        self._cache_bytes = 0

    async def close(self) -> None:
        await self.http.aclose()

    async def _image_part(self, ref: str) -> dict[str, Any] | None:
        if ref in self._image_cache:
            self._image_cache.move_to_end(ref)
            return self._image_cache[ref]
        data = b""
        if ref.startswith(("http://", "https://")):
            try:
                async with self.http.stream("GET",
                    ref, timeout=httpx.Timeout(20.0, connect=8.0), follow_redirects=True
                ) as response:
                    if response.is_error or int(response.headers.get("content-length") or 0) > self.MAX_IMAGE_BYTES:
                        return None
                    chunks, size = [], 0
                    async for block in response.aiter_bytes():
                        size += len(block)
                        if size > self.MAX_IMAGE_BYTES:
                            return None
                        chunks.append(block)
                    data = b"".join(chunks)
            except (httpx.HTTPError, ValueError):
                return None
        elif ref.startswith(("base64://", "data:image/")):
            encoded = ref[len("base64://"):] if ref.startswith("base64://") else ref.partition(",")[2]
            if len(encoded) > self.MAX_IMAGE_BYTES * 4 // 3 + 4:
                return None
            try:
                data = base64.b64decode(encoded, validate=True)
            except ValueError:
                return None
        mime = ("image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else
                "image/jpeg" if data.startswith(b"\xff\xd8\xff") else
                "image/gif" if data.startswith((b"GIF87a", b"GIF89a")) else
                "image/webp" if data.startswith(b"RIFF") and data[8:12] == b"WEBP" else "")
        if not mime or len(data) > self.MAX_IMAGE_BYTES:
            return None
        url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        part = {"type":"image_url", "image_url":{"url":url}}
        while self._image_cache and self._cache_bytes + len(url) > self.MAX_CACHE_BYTES:
            _, old = self._image_cache.popitem(last=False)
            self._cache_bytes -= len(old["image_url"]["url"])
        self._image_cache[ref] = part
        self._cache_bytes += len(url)
        return part

    async def describe(self, image_refs: list[str], context: str, settings: Settings) -> str:
        if not settings.vision_configured:
            raise ServiceError("尚未配置识图模型")
        parts: list[dict[str, Any]] = []
        for ref in image_refs:
            part = await self._image_part(ref)
            if part is not None:
                parts.append(part)
        if not parts:
            return ""
        text = settings.vision_prompt
        if context:
            text += f"\n（本轮聊天文字：{context}）"
        payload: dict[str, Any] = {
            "model": settings.vision_model,
            "messages": [{"role": "user", "content": [{"type": "text", "text": text}, *parts]}],
            "temperature": 0.2,
            "max_tokens": 600,
        }
        async with trace_request(self.request_log, kind="vision", settings=settings, request=payload,
                model=settings.vision_model, endpoint=settings.vision_base_url + "/chat/completions", feature="vision") as trace:
            return await self._describe_request(payload, settings, trace)

    async def _describe_request(self, payload, settings, trace):
        response = await self.http.post(
            f"{settings.vision_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.vision_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=httpx.Timeout(
                settings.vision_timeout, connect=min(10.0, settings.vision_timeout)
            ),
        )
        if response.is_error:
            raise ServiceError(f"识图模型请求失败：{_error_text(response)}")
        data: dict[str, Any] = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ServiceError("识图模型返回格式不正确") from exc
        if isinstance(content, list):
            content = "".join(
                str(item.get("text", "")) for item in content if isinstance(item, dict)
            )
        text = strip_think_blocks(str(content or "")).strip()
        trace.response = {"text": text}
        trace.usage = data.get("usage")
        return text


class MemoryClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        headers = {"Content-Type": "application/json"}
        if settings.memory_api_key:
            headers["Authorization"] = f"Bearer {settings.memory_api_key}"
        self.http = httpx.AsyncClient(
            base_url=settings.memory_url,
            headers=headers,
            timeout=httpx.Timeout(15.0, connect=3.0),
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.http.post(path, json=payload)
        if response.is_error:
            raise ServiceError(f"记忆服务请求失败：{_error_text(response)}")
        data = response.json()
        if not isinstance(data, dict):
            raise ServiceError("记忆服务返回格式不正确")
        return data

    async def health(self) -> dict[str, Any]:
        response = await self.http.get("/health")
        if response.is_error:
            raise ServiceError(f"记忆服务不健康：{_error_text(response)}")
        return response.json()

    async def recall(self, query: str, session_key: str, user_id: str) -> str:
        data = await self._post(
            "/recall",
            {"query": query, "session_key": session_key, "user_id": user_id},
        )
        return str(data.get("context") or "").strip()

    async def capture(
        self,
        user_content: str,
        assistant_content: str,
        session_key: str,
        user_id: str,
    ) -> None:
        await self._post(
            "/capture",
            {
                "user_content": user_content,
                "assistant_content": assistant_content,
                "session_key": session_key,
                "session_id": session_key,
                "user_id": user_id,
            },
        )

    async def search(self, query: str, limit: int = 5) -> str:
        data = await self._post("/search/memories", {"query": query, "limit": limit})
        return str(data.get("results") or "未找到相关记忆").strip()

    async def end_session(self, session_key: str, user_id: str) -> None:
        await self._post(
            "/session/end",
            {"session_key": session_key, "user_id": user_id},
        )
