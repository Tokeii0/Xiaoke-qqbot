from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING
from urllib.parse import urlsplit

import httpx
from nonebot import logger, on_request
from nonebot.adapters.onebot.v11 import Bot, GroupRequestEvent

from .clients import ServiceError

if TYPE_CHECKING:
    from .config import RuntimeConfigStore

# Some WAF/CDN-fronted endpoints drop requests without a browser UA (the server
# disconnects before responding), so we present a normal browser signature.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0"
)


def extract_allowed_qqs(data: Any, min_licenses: int) -> set[int]:
    """Parse the license-statistics response into the set of QQ numbers allowed to join.

    Response shape: ``{ "<product>": { "users": [ {"qq": "123",
    "lifetimeLicenseCount": N, "totalLicenseCount": M}, ... ] }, ... }``. A QQ
    qualifies when it has a record where BOTH counts are >= ``min_licenses``.
    """
    allowed: set[int] = set()
    if not isinstance(data, dict):
        return allowed
    threshold = max(1, int(min_licenses))
    for product in data.values():
        if not isinstance(product, dict):
            continue
        users = product.get("users")
        if not isinstance(users, list):
            continue
        for user in users:
            if not isinstance(user, dict):
                continue
            qq_raw = str(user.get("qq") or "").strip()
            if not qq_raw.isdigit():
                continue
            try:
                lifetime = float(user.get("lifetimeLicenseCount", 0) or 0)
                total = float(user.get("totalLicenseCount", 0) or 0)
            except (TypeError, ValueError):
                continue
            if lifetime >= threshold and total >= threshold:
                allowed.add(int(qq_raw))
    return allowed


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JoinGateService:
    """Holds the license allowlist, refreshes it from the API, persists it to disk."""

    def __init__(self, config_store: "RuntimeConfigStore", path: Path) -> None:
        self.config_store = config_store
        self.path = path
        self._lock = asyncio.Lock()
        self._allowed: set[int] = set()
        self._updated_at = ""
        self._loaded = False
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if isinstance(raw, dict):
            try:
                self._allowed = {int(q) for q in raw.get("qqs", [])}
            except (TypeError, ValueError):
                self._allowed = set()
            self._updated_at = str(raw.get("updated_at", ""))
            self._loaded = True

    def is_allowed(self, qq: int) -> bool:
        return qq in self._allowed

    @property
    def loaded(self) -> bool:
        return self._loaded

    def snapshot(self) -> dict[str, Any]:
        return {
            "count": len(self._allowed),
            "updated_at": self._updated_at,
            "loaded": self._loaded,
        }

    async def refresh(self) -> dict[str, Any]:
        settings = self.config_store.snapshot()
        url = settings.join_gate_api_url.strip()
        token = settings.join_gate_token.strip()
        if not url:
            raise ServiceError("尚未配置入群名单接口地址")
        if not token:
            raise ServiceError("尚未配置入群名单接口令牌")
        headers = {
            "authorization": f"Bearer {token}",
            "accept": "*/*",
            "user-agent": _BROWSER_UA,
        }
        parts = urlsplit(url)
        if parts.scheme and parts.netloc:
            headers["referer"] = f"{parts.scheme}://{parts.netloc}/"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
                response = await client.get(url, headers=headers, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise ServiceError(f"名单接口连接失败：{exc}") from exc
        if response.is_error:
            raise ServiceError(f"名单接口返回 HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ServiceError("名单接口返回的不是有效 JSON") from exc
        qqs = extract_allowed_qqs(data, settings.join_gate_min_licenses)
        async with self._lock:
            self._allowed = qqs
            self._updated_at = _now_iso()
            self._loaded = True
            await asyncio.to_thread(self._persist)
        return self.snapshot()

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(
                {"qqs": sorted(self._allowed), "updated_at": self._updated_at},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temp.replace(self.path)


def register_join_gate(config_store: "RuntimeConfigStore", service: JoinGateService) -> None:
    matcher = on_request()

    @matcher.handle()
    async def handle_join_request(bot: Bot, event: GroupRequestEvent) -> None:
        if event.sub_type != "add":  # only join requests, not bot invites
            return
        settings = config_store.snapshot()
        if not settings.join_gate_enabled:
            return
        if int(event.group_id) != settings.join_gate_group:
            return
        qq = int(event.user_id)
        if not service.loaded:
            logger.warning(f"入群名单尚未成功加载，本次请求交由人工处理：user={qq}")
            return
        approved = qq in settings.superusers or service.is_allowed(qq)
        try:
            if approved:
                await bot.set_group_add_request(
                    flag=event.flag, sub_type=event.sub_type, approve=True
                )
            else:
                await bot.set_group_add_request(
                    flag=event.flag,
                    sub_type=event.sub_type,
                    approve=False,
                    reason=settings.join_gate_reject_reason,
                )
            logger.info(
                f"入群管理：group={event.group_id} user={qq} -> {'通过' if approved else '拒绝'}"
            )
        except Exception as exc:
            logger.warning(f"入群请求处理失败 user={qq}：{exc}")
