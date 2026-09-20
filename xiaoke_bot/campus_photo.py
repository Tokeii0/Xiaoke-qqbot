"""First-person campus illustrations grounded in the current fictional routine."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
from nonebot import logger

from .clients import ServiceError
from .routine import PHOTO_SCENES, local_now, routine_is_sleeping, routine_snapshot
from .routine_diary import RoutineDiary, event_id
from .request_log import trace_request


@dataclass(frozen=True)
class CampusPhoto:
    data: bytes
    size: str
    activity: str
    scene: str
    time: str
    event_id: str = ""


def photo_prompt(settings, snapshot: dict, scene: str) -> str:
    hour = int(snapshot["local_time"].split(":")[0])
    light = ("深夜，天空黑暗，只有路灯或室内照明" if hour < 5 or hour >= 19 else
             "清晨，天色刚亮，柔和低角度光线" if hour < 7 else
             "上午的自然日光" if hour < 11 else "中午明亮的自然光" if hour < 14 else
             "下午的自然光" if hour < 17 else "傍晚，渐暗天色与暖色照明")
    return (
        "生成一张非常自然的大学生日常手机照片风格的虚构场景配图，纪实摄影质感，轻微随手构图，"
        "正常手机镜头与曝光，物品有使用痕迹，不要广告大片、电影调色或精致摆拍。\n"
        f"统一校园环境：{settings.routine_photo_campus}\n"
        f"当地时间：{snapshot['date']} {snapshot['weekday']} {snapshot['local_time']}，时区 {snapshot['timezone']}。"
        f"光线必须符合：{light}；植物与衣着符合月份，不声称这是实测天气。\n"
        f"当前真实使用的角色日程：{snapshot['current']['activity']}。场景类别：{PHOTO_SCENES[scene]}。\n"
        "照片只拍这件事当下眼前能看到的环境、物品与活动。第一人称向外看的手机后置镜头视角："
        "运动拍球场或跑道，吃饭拍眼前餐盘，散步拍小路，学习拍书本桌面。"
        "不要自拍、人物肖像、拍摄者本人、身体或手脚，也不要镜面里的拍摄者或手机倒影。"
        "远处可有不占主体的模糊路人；不出现可识别的人脸、个人信息、精确课表或聊天屏幕。"
        "不要拼图、边框、时间戳、文字标题；不出现真实学校名称，不将未来活动画进当前画面。"
    )


class CampusPhotoClient:
    def __init__(self, state_path: Path | None = None, *, request_log=None) -> None:
        self.request_log = request_log
        self.state_path = state_path
        self._lock = asyncio.Lock()
        self.diary = RoutineDiary(state_path.with_suffix(".diary.db") if state_path else None)
        try:
            saved = json.loads(state_path.read_text(encoding="utf-8")) if state_path else {}
            self._sent = {key: value for key, value in saved.items() if isinstance(value, dict)}
        except (OSError, ValueError, AttributeError):
            self._sent = {}

    def _save(self) -> None:
        self._sent = dict(sorted(self._sent.items(), key=lambda item: item[1].get("attempt_at", 0))[-512:])
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.state_path.with_suffix(".tmp")
            temp.write_text(json.dumps(self._sent, ensure_ascii=False), encoding="utf-8")
            temp.replace(self.state_path)

    async def generate(self, settings, snapshot: dict, scene: str) -> CampusPhoto:
        async with trace_request(self.request_log, kind="image", settings=settings, model=settings.routine_photo_model,
                feature="campus_photo", endpoint=settings.routine_photo_api_base_url + "/images/generations",
                request={"scene": scene, "snapshot": snapshot, "quality": settings.routine_photo_quality,
                         "ratio": settings.routine_photo_ratio, "campus": settings.routine_photo_campus}) as trace:
            photo = await self._generate_image(settings, snapshot, scene, trace)
            trace.response = {"bytes": len(photo.data), "size": photo.size, "activity": photo.activity, "time": photo.time}
            return photo

    async def _generate_image(self, settings, snapshot, scene, trace):
        if scene not in PHOTO_SCENES or scene == "none" or not settings.routine_photo_key:
            raise ServiceError("校园照片尚未配置可用的 Key 或场景")
        identity = f"{snapshot['date']}|{snapshot['current']['start']}|{snapshot['current']['activity']}"
        ratio = settings.routine_photo_ratio
        if ratio == "auto":
            ratio = ("4:3", "3:4")[hashlib.sha256(identity.encode()).digest()[0] % 2]
        size = "1024x768" if ratio == "4:3" else "768x1024"
        payload = {"model": settings.routine_photo_model, "prompt": photo_prompt(settings, snapshot, scene),
                   "size": size, "quality": settings.routine_photo_quality, "n": 1, "output_format": "jpeg"}
        trace.request = payload
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10), follow_redirects=False) as client:
                response = await client.post(settings.routine_photo_api_base_url.rstrip("/") + "/images/generations",
                    headers={"Authorization": "Bearer " + settings.routine_photo_key}, json=payload)
            if response.is_error or response.is_redirect:
                raise ServiceError(f"校园生图接口返回 HTTP {response.status_code}")
            if len(response.content) > 24 * 1024 * 1024:
                raise ServiceError("校园照片响应过大")
            body = response.json()
            trace.usage = body.get("usage")
            encoded = body["data"][0]["b64_json"]
            data = base64.b64decode(encoded, validate=True)
            if not data.startswith(b"\xff\xd8\xff") or len(data) > 16 * 1024 * 1024:
                raise ServiceError("生图接口没有返回有效的 JPEG 照片")
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ServiceError("校园照片生成失败，请检查模型、接口和 Key") from exc
        return CampusPhoto(data, size, snapshot["current"]["activity"], scene,
                           f"{snapshot['date']} {snapshot['local_time']}")

    async def maybe_send(self, *, settings, verdict, scope: str, send,
                         request_id: str = "", now: datetime | None = None) -> CampusPhoto | None:
        if not (settings.routine_enabled and settings.routine_photo_configured and settings.jev_enabled and verdict):
            return None
        scene = verdict.routine_photo
        if verdict.routine_photo_score is not None and verdict.routine_photo_score < settings.routine_photo_threshold:
            return None
        if scene not in PHOTO_SCENES or scene == "none" or verdict.routine_reaction == "focus":
            return None
        snapshot = routine_snapshot(settings, now)
        current = snapshot["current"]
        selection = verdict.routine_photo_event
        if selection == "none":
            return None
        requested = selection != "current"
        if current["kind"] in {"睡觉", "上课"}:
            return None
        if not requested and verdict.routine_photo_context and verdict.routine_photo_context != (snapshot["date"], current["start"], current["activity"]):
            return None
        identity = selection if requested else event_id(settings.bot_name, snapshot["date"], current)
        try:
            self.diary.observe(settings, snapshot=snapshot)
            record = self.diary.get(identity)
        except Exception as exc:
            logger.warning(f"校园照片记录暂不可用：{type(exc).__name__}")
            return None
        if (not record or record["bot_name"] != settings.bot_name or record["day"] != snapshot["date"]
                or record["start"] > snapshot["local_time"] or record["kind"] in {"睡觉", "上课"}
                or any(word in record["activity"] for word in ("宿舍", "被窝", "床"))):
            return None
        # Concurrent questions wait for the canonical image, then each requester gets the same bytes.
        async with self._lock:
            stamp = local_now(settings, now).timestamp()
            sent = self._sent.get(scope, {})
            requests = sent.get("requests", [])
            if request_id and request_id in requests:
                return None
            if not requested and (sent.get("event_id") == identity
                    or stamp - sent.get("sent_at", 0) < settings.routine_photo_cooldown_minutes * 60):
                return None
            latest = routine_snapshot(settings, now)
            if (latest["date"] != record["day"] or latest["current"]["kind"] in {"睡觉", "上课"}
                    or (not requested and latest["current"] != current)):
                return None
            self._sent[scope] = {**sent, "attempt_at": stamp}
            try:
                self._save()
                # A saved photo seals the facts as well; a later enrichment must never change them.
                self.diary.seal(identity)
                record = self.diary.get(identity)
                if record["photo"] is None:
                    if stamp - record["attempted_at"] < 300:
                        return None
                    self.diary.attempt(identity, stamp)
                    generated = await self.generate(settings, json.loads(record["snapshot"]), scene)
                    self.diary.save_photo(identity, generated)
                    record = self.diary.get(identity)
                photo = CampusPhoto(record["photo"], record["photo_size"], record["details"],
                                    record["photo_scene"], record["photo_time"], identity)
                latest = routine_snapshot(settings, now)
                if (routine_is_sleeping(settings, now) or latest["date"] != record["day"]
                        or latest["current"]["kind"] in {"睡觉", "上课"}
                        or (not requested and latest["current"] != current)):
                    return None
                await send(photo.data)
                self._sent[scope] = {"attempt_at": stamp, "sent_at": stamp, "event_id": identity,
                                     "requests": (requests + [request_id])[-64:] if request_id else requests}
                self._save()
                return photo
            except Exception as exc:
                # The ordinary text/voice reply has already been delivered; a photo failure is quiet.
                logger.warning(f"校园照片未发送：{type(exc).__name__}")
                return None
