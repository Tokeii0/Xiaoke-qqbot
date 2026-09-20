from __future__ import annotations

import base64
import asyncio
import hmac
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import httpx
from nonebot import get_app, get_bots, logger
from pydantic import BaseModel, Field, SecretStr

from .clients import ServiceError
from .config import DEFAULT_VOICE_INSTRUCTIONS, DEFAULT_VOICE_JEV_PROMPT, DEFAULT_VOICE_JEV_GATE_PROMPT, DEFAULT_JEV_BEHAVIOR_PROMPT, RuntimeConfigStore
from .judge import JevClient
from .daily_summary import send_summary_to_group
from .webhook import build_webhook_message
from .voice import LiveVoiceClient, plan_voice_reply
from .routine import DEFAULT_CAMPUS, DEFAULT_WEEKDAY_SCHEDULE, DEFAULT_WEEKEND_SCHEDULE, routine_snapshot
from .campus_photo import CampusPhotoClient
from .request_log import RequestLogStore, call_scope
from .web_search import WebSearchClient


class LoginPayload(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class KeywordPromptRulePayload(BaseModel):
    name: str
    keywords: list[str] = Field(default_factory=list)
    prompt: str
    enabled: bool = True
    probability: float = 1.0


class AdminConfigPayload(BaseModel):
    search_enabled: bool = False
    search_api_base_url: str = "https://api.tavily.com"
    search_api_key: SecretStr | None = None
    clear_search_api_key: bool = False
    search_threshold: float = Field(default=0.75, ge=0, le=1)
    search_max_results: int = Field(default=5, ge=1, le=8)
    search_timeout: float = Field(default=15, ge=3, le=30)
    typo_enabled: bool = False
    typo_probability: float = Field(default=0.04, ge=0, le=0.15)
    typo_cooldown_minutes: int = Field(default=30, ge=5, le=1440)
    routine_enabled: bool = False
    routine_sleep_silent: bool = True
    routine_photo_enabled: bool = False
    routine_photo_api_base_url: str = "https://api.openai.com/v1"
    routine_photo_api_key: SecretStr | None = None
    clear_routine_photo_api_key: bool = False
    routine_photo_model: str = "gpt-image-2.5-flare"
    routine_photo_quality: Literal["low", "medium", "high"] = "medium"
    routine_photo_ratio: Literal["auto", "4:3", "3:4"] = "auto"
    routine_photo_trigger: Literal["jev", "always"] = "jev"
    routine_photo_threshold: float = Field(default=0.75, ge=0, le=1)
    routine_photo_cooldown_minutes: int = Field(default=30, ge=1, le=1440)
    routine_photo_campus: str = Field(default=DEFAULT_CAMPUS, min_length=1, max_length=2000)
    routine_variation: Literal["fixed", "natural", "rich"] = "rich"
    routine_weekday_schedule: str = Field(default=DEFAULT_WEEKDAY_SCHEDULE, max_length=8000)
    routine_weekend_schedule: str = Field(default=DEFAULT_WEEKEND_SCHEDULE, max_length=8000)
    jev_scene_enabled: bool = False
    jev_continuity_enabled: bool = False
    jev_memory_enabled: bool = False
    jev_followup_enabled: bool = False
    jev_tools_enabled: bool = False
    jev_knowledge_enabled: bool = False
    jev_feedback_enabled: bool = False
    jev_knowledge_max_age_days: int = Field(default=90, ge=1, le=3650)
    vision_mode: Literal["direct", "separate"] = "separate"
    vision_context_images: int = Field(default=6, ge=1, le=12)
    jev_behavior_prompt: str = Field(default=DEFAULT_JEV_BEHAVIOR_PROMPT, max_length=2000)
    jev_followup_min_hours: float = Field(default=12, ge=1, le=720)
    bot_name: str
    superusers: list[int]
    allowed_groups: list[int]
    allow_superuser_private_chat: bool
    respond_without_at: bool
    probability_reply_enabled: bool
    reply_probability: float
    trigger_keywords: list[str] = Field(default_factory=list)
    history_messages: int
    max_reply_chars: int
    quote_reply_enabled: bool
    quote_reply_probability: float
    segment_send_enabled: bool
    segment_probability: float
    segment_max_parts: int
    segment_delay_min: float
    segment_delay_max: float
    humanize_remove_punctuation: bool
    humanize_newline_to_space: bool
    humanize_delay_enabled: bool
    humanize_delay_min: float
    humanize_delay_max: float
    moderation_enabled: bool
    moderation_keywords: list[str] = Field(default_factory=list)
    moderation_exempt_admins: bool
    member_analysis_enabled: bool
    member_analysis_auto: bool
    member_analysis_min_messages: int
    member_analysis_interval_messages: int
    member_analysis_sample_limit: int
    member_message_retention: int
    member_profile_in_reply: bool
    mood_in_reply: bool
    mood_half_life_hours: float
    mood_event_nudges_enabled: bool
    favorability_decay_enabled: bool
    favorability_half_life_days: float
    proactive_enabled: bool
    proactive_quiet_start: int
    proactive_quiet_end: int
    proactive_hourly_cap: int
    proactive_daily_cap: int
    proactive_cooldown_seconds: int
    api_base_url: str
    model: str
    temperature: float
    max_tokens: int
    top_p: float
    presence_penalty: float
    frequency_penalty: float
    seed: int | None = None
    reasoning_effort: str = ""
    response_format: str = "text"
    stop_sequences: list[str] = Field(default_factory=list)
    request_timeout: float
    extra_body_json: str = "{}"
    prompt_identity: str
    prompt_personality: str
    prompt_speaking_style: str
    prompt_group_behavior: str
    prompt_response_preferences: str
    prompt_boundaries: str
    prompt_interests: str = Field(default="", max_length=4000)
    context_timezone: str
    keyword_prompt_rules: list[KeywordPromptRulePayload] = Field(default_factory=list)
    system_prompt: str
    vision_enabled: bool
    vision_api_base_url: str
    vision_model: str
    vision_max_images: int
    vision_skip_stickers: bool
    vision_prompt: str
    vision_timeout: float
    webhook_enabled: bool
    webhook_token: str
    webhook_target_group: int
    webhook_prefix: str
    webhook_template: str
    fallback_enabled: bool
    fallback_api_base_url: str
    fallback_model: str
    offense_guard_enabled: bool
    offense_prompt: str
    offense_action: str
    offense_mute_duration: int
    offense_threshold: int
    offense_include_admins: bool
    jev_enabled: bool
    jev_model: str
    jev_timeout: float = Field(ge=1, le=30)
    jev_gate_enabled: bool
    jev_offense_enabled: bool
    jev_addressed_threshold: float = Field(ge=0, le=1)
    jev_worth_threshold: float = Field(ge=0, le=1)
    jev_offense_threshold: float = Field(ge=0, le=1)
    # A Score answer is a weighted position across 3 ordered levels, so 0-2.
    jev_max_intrusion: float = Field(ge=0, le=2)
    jev_min_text_length: int = Field(ge=1, le=200)
    jev_context_messages: int = Field(ge=0, le=40)
    jev_use_proactive_budget: bool
    jev_log_enabled: bool
    jev_log_retention: int = Field(ge=0, le=5000)
    jev_addressed_prompt: str = Field(default="", max_length=2000)
    jev_worth_prompt: str = Field(default="", max_length=2000)
    jev_intrusion_levels: list[str] = Field(default_factory=list)
    jev_interest_prompt: str = Field(default="", max_length=2000)
    jev_interest_threshold: float = Field(ge=0, le=1)
    join_gate_enabled: bool
    join_gate_group: int
    join_gate_api_url: str
    join_gate_min_licenses: int
    join_gate_refresh_hours: int
    join_gate_reject_reason: str
    summary_enabled: bool
    summary_hour: int
    summary_min_messages: int
    summary_send_enabled: bool
    api_key: SecretStr | None = None
    clear_api_key: bool = False
    vision_api_key: SecretStr | None = None
    clear_vision_api_key: bool = False
    fallback_api_key: SecretStr | None = None
    clear_fallback_api_key: bool = False
    join_gate_token: SecretStr | None = None
    clear_join_gate_token: bool = False
    voice_enabled: bool = False
    voice_api_base_url: str = "https://api.openai.com/v1"
    voice_model: str = "gpt-live-1"
    voice_name: str = "marin"
    voice_instructions: str = DEFAULT_VOICE_INSTRUCTIONS
    voice_pace: str = "natural"
    voice_reply_probability: float = Field(default=1.0, ge=0, le=1)
    voice_send_text: bool = True
    voice_max_chars: int = Field(default=300, ge=20, le=1000)
    voice_timeout: float = Field(default=90, ge=15, le=180)
    voice_silence_seconds: float = Field(default=3, ge=1, le=5)
    voice_api_key: SecretStr | None = None
    clear_voice_api_key: bool = False
    voice_jev_enabled: bool = False
    voice_jev_prompt: str = Field(default=DEFAULT_VOICE_JEV_PROMPT, max_length=2000)
    voice_jev_gate_enabled: bool = False
    voice_jev_gate_prompt: str = Field(default=DEFAULT_VOICE_JEV_GATE_PROMPT, max_length=2000)


class ForceWakePayload(BaseModel):
    reason: str = Field(min_length=1, max_length=300)


class SearchPreviewPayload(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    search_api_base_url: str = "https://api.tavily.com"
    search_api_key: SecretStr | None = None
    clear_search_api_key: bool = False
    search_max_results: int = Field(default=5, ge=1, le=8)
    search_timeout: float = Field(default=15, ge=3, le=30)


class CampusPhotoPreviewPayload(BaseModel):
    routine_photo_api_base_url: str = "https://api.openai.com/v1"
    routine_photo_api_key: SecretStr | None = None
    clear_routine_photo_api_key: bool = False
    routine_photo_model: str = "gpt-image-2.5-flare"
    routine_photo_quality: Literal["low", "medium", "high"] = "medium"
    routine_photo_ratio: Literal["auto", "4:3", "3:4"] = "auto"
    routine_photo_campus: str = Field(default=DEFAULT_CAMPUS, min_length=1, max_length=2000)
    scene: Literal["campus", "dining", "study", "sport", "street"] = "campus"


class VoicePreviewPayload(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    context: str = Field(default="", max_length=1000)
    voice_api_base_url: str = "https://api.openai.com/v1"
    voice_model: str = "gpt-live-1"
    voice_name: str = "marin"
    voice_instructions: str = DEFAULT_VOICE_INSTRUCTIONS
    voice_pace: str = "natural"
    voice_max_chars: int = Field(default=300, ge=20, le=1000)
    voice_timeout: float = Field(default=90, ge=15, le=180)
    voice_silence_seconds: float = Field(default=3, ge=1, le=5)
    voice_api_key: SecretStr | None = None
    clear_voice_api_key: bool = False
    voice_jev_enabled: bool = False
    voice_jev_prompt: str = Field(default=DEFAULT_VOICE_JEV_PROMPT, max_length=2000)
    voice_jev_gate_enabled: bool = False
    voice_jev_gate_prompt: str = Field(default=DEFAULT_VOICE_JEV_GATE_PROMPT, max_length=2000)


class ModelListPayload(BaseModel):
    api_base_url: str
    api_key: SecretStr | None = None


class MemberUpdatePayload(BaseModel):
    favorability: float = Field(ge=0, le=100)
    admin_note: str = Field(default="", max_length=2000)
    bot_nickname: str = Field(default="", max_length=50)
    offense_count: int = Field(default=0, ge=0, le=1000)


bearer = HTTPBearer(auto_error=False)


def _admin_token() -> str:
    return os.getenv("WEB_ADMIN_TOKEN", "").strip()


def _enabled() -> bool:
    return os.getenv("WEB_ADMIN_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _matches(provided: str) -> bool:
    expected = _admin_token()
    return bool(expected and hmac.compare_digest(provided, expected))


async def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要管理令牌")
    if not _matches(credentials.credentials):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理令牌无效")


def register_web_admin(
    config_store: RuntimeConfigStore,
    memory_client: Any,
    member_analysis: Any,
    join_gate: Any,
    daily_summary: Any,
    judge_log: Any,
    voice_client: LiveVoiceClient | None = None,
    jev_client: JevClient | None = None,
    intelligence: Any = None,
    campus_photo_client: CampusPhotoClient | None = None,
    request_log: RequestLogStore | None = None,
    web_search_client: WebSearchClient | None = None,
) -> None:
    if not _enabled():
        logger.info("Web 管理后台已禁用")
        return
    if not _admin_token():
        logger.error("WEB_ADMIN_TOKEN 未设置，出于安全考虑不启用 Web 管理后台")
        return

    app = get_app()
    router = APIRouter()
    web_dir = Path(__file__).resolve().parent / "web"
    request_log = request_log or RequestLogStore()
    voice_client = voice_client or LiveVoiceClient(request_log)
    campus_photo_client = campus_photo_client or CampusPhotoClient(request_log=request_log)
    web_search_client = web_search_client or WebSearchClient(request_log)

    @router.get("/admin", include_in_schema=False)
    @router.get("/admin/", include_in_schema=False)
    async def admin_index() -> FileResponse:
        return FileResponse(web_dir / "index.html", headers={"Cache-Control": "no-store"})

    @router.get("/admin/styles.css", include_in_schema=False)
    async def admin_styles() -> FileResponse:
        return FileResponse(
            web_dir / "styles.css",
            media_type="text/css",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/admin/app.js", include_in_schema=False)
    async def admin_script() -> FileResponse:
        return FileResponse(
            web_dir / "app.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/admin/api/login", include_in_schema=False)
    async def login(payload: LoginPayload, request: Request) -> JSONResponse:
        if request.client and request.client.host not in {"127.0.0.1", "::1"}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅允许本机访问")
        if not _matches(payload.token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理令牌无效")
        return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})

    @router.get("/admin/api/config", dependencies=[Depends(require_admin)])
    async def read_config() -> dict[str, Any]:
        return config_store.public_dict()

    @router.put("/admin/api/config", dependencies=[Depends(require_admin)])
    async def save_config(payload: AdminConfigPayload) -> dict[str, Any]:
        try:
            values = payload.model_dump(
                exclude={
                    "api_key",
                    "clear_api_key",
                    "vision_api_key",
                    "clear_vision_api_key",
                    "fallback_api_key",
                    "clear_fallback_api_key",
                    "join_gate_token",
                    "clear_join_gate_token",
                    "voice_api_key",
                    "clear_voice_api_key",
                    "routine_photo_api_key", "clear_routine_photo_api_key",
                    "search_api_key", "clear_search_api_key",
                }
            )
            config_store.update(values)
            if payload.clear_search_api_key:
                config_store.set_search_api_key("")
            elif payload.search_api_key and payload.search_api_key.get_secret_value().strip():
                config_store.set_search_api_key(payload.search_api_key.get_secret_value())
            if payload.clear_routine_photo_api_key:
                config_store.set_routine_photo_api_key("")
            elif payload.routine_photo_api_key and payload.routine_photo_api_key.get_secret_value().strip():
                config_store.set_routine_photo_api_key(payload.routine_photo_api_key.get_secret_value())
            if payload.clear_api_key:
                config_store.set_api_key("")
            elif payload.api_key and payload.api_key.get_secret_value().strip():
                config_store.set_api_key(payload.api_key.get_secret_value())
            if payload.clear_vision_api_key:
                config_store.set_vision_api_key("")
            elif payload.vision_api_key and payload.vision_api_key.get_secret_value().strip():
                config_store.set_vision_api_key(payload.vision_api_key.get_secret_value())
            if payload.clear_fallback_api_key:
                config_store.set_fallback_api_key("")
            elif payload.fallback_api_key and payload.fallback_api_key.get_secret_value().strip():
                config_store.set_fallback_api_key(payload.fallback_api_key.get_secret_value())
            if payload.clear_join_gate_token:
                config_store.set_join_gate_token("")
            elif payload.join_gate_token and payload.join_gate_token.get_secret_value().strip():
                config_store.set_join_gate_token(payload.join_gate_token.get_secret_value())
            if payload.clear_voice_api_key:
                config_store.set_voice_api_key("")
            elif payload.voice_api_key and payload.voice_api_key.get_secret_value().strip():
                config_store.set_voice_api_key(payload.voice_api_key.get_secret_value())
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        logger.info("Web 管理后台已更新机器人运行配置")
        return {"ok": True, "config": config_store.public_dict()}

    @router.post("/admin/api/search/preview", dependencies=[Depends(require_admin)])
    async def preview_search(payload: SearchPreviewPayload):
        try:
            values = payload.model_dump(exclude={"query", "search_api_key", "clear_search_api_key"})
            values = {key: config_store._normalize(key, value) for key, value in values.items()}
            if payload.clear_search_api_key:
                values["search_api_key"] = ""
            elif payload.search_api_key:
                values["search_api_key"] = payload.search_api_key.get_secret_value().strip()
            settings = replace(config_store.snapshot(), **values)
            with call_scope("admin_preview"):
                results = await web_search_client.search(payload.query, settings)
            return JSONResponse({"results": results}, headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ServiceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/admin/api/voice/preview", dependencies=[Depends(require_admin)])
    async def preview_voice(payload: VoicePreviewPayload) -> JSONResponse:
        try:
            values = payload.model_dump(exclude={"text", "context", "voice_api_key", "clear_voice_api_key"})
            values = {key: config_store._normalize(key, value) for key, value in values.items()}
            settings = config_store.snapshot()
            if payload.clear_voice_api_key:
                values["voice_api_key"] = ""
            elif payload.voice_api_key and payload.voice_api_key.get_secret_value().strip():
                values["voice_api_key"] = payload.voice_api_key.get_secret_value().strip()
            settings = replace(settings, **values)
            if not settings.voice_api_key:
                raise ServiceError("请先配置拥有 GPT-Live 访问权限的语音 API Key")
            recent = [{"speaker": "试听情境", "is_bot": False, "text": payload.context}] if payload.context.strip() else []
            with call_scope("admin_preview"):
                plan = await plan_voice_reply(payload.text, settings, jev_client, recent)
                if not plan.allowed:
                    return JSONResponse({"voice_allowed": False, "voice_reason": plan.reason,
                                         "voice_style": plan.style}, headers={"Cache-Control": "no-store"})
                # Preview consumes the current form without saving it or sending to QQ.
                clip = await voice_client.generate(payload.text, plan.settings)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ServiceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return JSONResponse({
            "audio_base64": base64.b64encode(clip.wav).decode("ascii"),
            "media_type": "audio/wav",
            "transcript": clip.transcript,
            "duration_seconds": round(clip.duration_seconds, 2),
            "usage_seconds": clip.usage_seconds,
            "voice_style": plan.style,
            "voice_allowed": True,
            "voice_reason": plan.reason,
        }, headers={"Cache-Control": "no-store"})

    @router.post("/admin/api/models", dependencies=[Depends(require_admin)])
    async def list_models(payload: ModelListPayload) -> dict[str, Any]:
        base_url = payload.api_base_url.strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="模型接口地址必须以 http:// 或 https:// 开头",
            )
        provided_key = payload.api_key.get_secret_value().strip() if payload.api_key else ""
        api_key = provided_key or config_store.snapshot().api_key
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="请先填写 API Key",
            )
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=8.0)) as client:
                response = await client.get(
                    f"{base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            if response.is_error:
                detail = f"模型接口返回 HTTP {response.status_code}"
                try:
                    body = response.json()
                    if isinstance(body, dict):
                        error = body.get("error")
                        if isinstance(error, dict):
                            detail = str(error.get("message") or detail)
                        elif error:
                            detail = str(error)
                except ValueError:
                    pass
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)
            body = response.json()
            raw_models = body.get("data", body.get("models", [])) if isinstance(body, dict) else []
            models: list[str] = []
            if isinstance(raw_models, list):
                for item in raw_models:
                    model_id = item.get("id") if isinstance(item, dict) else item
                    if isinstance(model_id, str) and model_id.strip():
                        models.append(model_id.strip())
            return {"models": sorted(set(models), key=str.lower), "count": len(set(models))}
        except HTTPException:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"无法拉取模型列表：{exc}",
            ) from exc

    @router.get("/admin/api/status", dependencies=[Depends(require_admin)])
    async def runtime_status() -> dict[str, Any]:
        try:
            memory = await memory_client.health()
        except Exception as exc:
            memory = {"status": "unavailable", "error": str(exc)}
        bots = get_bots()
        return {
            "nonebot": "ok",
            "connected_bots": [str(bot_id) for bot_id in bots],
            "memory": memory,
            "config_file": str(config_store.path),
        }

    @router.get("/admin/api/routine", dependencies=[Depends(require_admin)])
    async def read_routine(offset: int = 0) -> dict[str, Any]:
        if offset not in {0, 1}:
            raise HTTPException(status_code=422, detail="只能查看今天或明天的安排")
        settings = config_store.snapshot()
        plan = routine_snapshot(settings, offset=offset)
        plan["diary"] = [row for row in campus_photo_client.diary.entries(settings, snapshot=plan) if row["sealed"]] if offset == 0 else []
        return plan

    @router.post("/admin/api/routine/wake", dependencies=[Depends(require_admin)])
    async def force_routine_wake(payload: ForceWakePayload) -> JSONResponse:
        if not payload.reason.strip():
            raise HTTPException(status_code=422, detail="请填写起床理由")
        try:
            wake = campus_photo_client.diary.force_wake(config_store.snapshot(), payload.reason)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse({"wake": wake}, headers={"Cache-Control": "no-store"})

    @router.post("/admin/api/routine/photo/preview", dependencies=[Depends(require_admin)])
    async def preview_campus_photo(payload: CampusPhotoPreviewPayload) -> JSONResponse:
        try:
            values = payload.model_dump(exclude={"scene", "routine_photo_api_key", "clear_routine_photo_api_key"})
            values = {key: config_store._normalize(key, value) for key, value in values.items()}
            if payload.clear_routine_photo_api_key:
                values["routine_photo_api_key"] = ""
            elif payload.routine_photo_api_key and payload.routine_photo_api_key.get_secret_value().strip():
                values["routine_photo_api_key"] = payload.routine_photo_api_key.get_secret_value().strip()
            settings = replace(config_store.snapshot(), **values)
            snapshot = routine_snapshot(settings)
            # Preview uses the current local light but an explicit sample scene, even at bedtime.
            activities = {"campus": "在校园小路散步", "dining": "在食堂吃鸡腿饭", "study": "在图书馆看书",
                          "sport": "在操场慢跑的间隙休息", "street": "在学校附近的书店逛逛"}
            snapshot["current"] = {**snapshot["current"], "activity": activities[payload.scene]}
            with call_scope("admin_preview"):
                photo = await campus_photo_client.generate(settings, snapshot, payload.scene)
            return JSONResponse({"image_base64": base64.b64encode(photo.data).decode(), "mime_type": "image/jpeg",
                "activity": photo.activity, "time": photo.time, "size": photo.size, "model": settings.routine_photo_model},
                headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ServiceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/admin/api/intelligence", dependencies=[Depends(require_admin)])
    async def read_intelligence(group_id: int | None = None) -> dict[str, Any]:
        if intelligence is None:
            raise HTTPException(status_code=503, detail="语义行为服务尚未加载，请重启机器人")
        scope = f"agent:qq-group-{group_id}:group:{group_id}" if group_id is not None else None
        return {kind: await intelligence.store.rows(kind, scope=scope, limit=100)
                for kind in ("facts", "topics", "reminders", "knowledge", "preferences")}

    @router.post("/admin/api/intelligence/{kind}/{record_id}/close", dependencies=[Depends(require_admin)])
    async def close_intelligence_record(kind: str, record_id: int) -> dict[str, Any]:
        if intelligence is None:
            raise HTTPException(status_code=503, detail="语义行为服务尚未加载")
        if kind not in {"facts", "topics", "reminders", "knowledge", "preferences"}:
            raise HTTPException(status_code=404, detail="未知记录类型")
        if not await intelligence.store.close_record(kind, record_id):
            raise HTTPException(status_code=409, detail="记录不存在或状态已改变，请刷新列表")
        return {"ok": True}

    @router.get("/admin/api/requests", dependencies=[Depends(require_admin)])
    async def list_requests(kind: str = "", feature: str = "", status: str = "", group_id: int | None = None,
                            query: str = "", offset: int = 0, limit: int = 50):
        result = await asyncio.to_thread(request_log.recent, kind=kind, feature=feature, status=status,
            group_id=group_id, query=query, offset=offset, limit=limit)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.get("/admin/api/requests/{request_id}", dependencies=[Depends(require_admin)])
    async def request_detail(request_id: int):
        result = await asyncio.to_thread(request_log.detail, request_id)
        if result is None:
            raise HTTPException(status_code=404, detail="请求记录不存在或已超过保留上限")
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.get("/admin/api/jev-logs", dependencies=[Depends(require_admin)])
    async def list_jev_logs(
        group_id: int | None = None,
        limit: int = 100,
        outcome: str = "all",
        hours: int = 24,
    ) -> dict[str, Any]:
        """Recent Jev judgments plus a rollup for the header tiles.

        `outcome` filters by what the bot did: all / replied / silent / offended.
        """
        replied: bool | None = None
        offended: bool | None = None
        if outcome == "replied":
            replied = True
        elif outcome == "silent":
            replied = False
        elif outcome == "offended":
            offended = True
        items = await judge_log.recent(
            limit=limit, group_id=group_id, replied=replied, offended=offended
        )
        return {
            "items": items,
            "stats": await judge_log.stats(hours=hours),
            "config": {
                "jev_enabled": config_store.snapshot().jev_enabled,
                "jev_log_enabled": config_store.snapshot().jev_log_enabled,
                "addressed_threshold": config_store.snapshot().jev_addressed_threshold,
                "worth_threshold": config_store.snapshot().jev_worth_threshold,
                "offense_threshold": config_store.snapshot().jev_offense_threshold,
                "interest_threshold": config_store.snapshot().jev_interest_threshold,
                "max_intrusion": config_store.snapshot().jev_max_intrusion,
            },
        }

    @router.delete("/admin/api/jev-logs", dependencies=[Depends(require_admin)])
    async def clear_jev_logs() -> dict[str, Any]:
        removed = await judge_log.clear()
        logger.info(f"Web 管理后台已清空 Jev 判定日志（{removed} 条）")
        return {"ok": True, "removed": removed}

    @router.get("/admin/api/members", dependencies=[Depends(require_admin)])
    async def list_members(
        group_id: int | None = None,
        query: str = "",
        limit: int = 200,
    ) -> dict[str, Any]:
        members = await member_analysis.store.list_members(
            group_id=group_id,
            query=query,
            limit=limit,
        )
        return {"members": members, "stats": await member_analysis.store.stats()}

    @router.get(
        "/admin/api/members/{group_id}/{user_id}",
        dependencies=[Depends(require_admin)],
    )
    async def read_member(group_id: int, user_id: int) -> dict[str, Any]:
        member = await member_analysis.store.get_member(group_id, user_id)
        if member is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="群员画像不存在")
        return {"member": member}

    @router.patch(
        "/admin/api/members/{group_id}/{user_id}",
        dependencies=[Depends(require_admin)],
    )
    async def update_member(
        group_id: int,
        user_id: int,
        payload: MemberUpdatePayload,
    ) -> dict[str, Any]:
        member = await member_analysis.store.update_member(
            group_id,
            user_id,
            payload.favorability,
            payload.admin_note,
            payload.bot_nickname,
            payload.offense_count,
        )
        if member is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="群员画像不存在")
        return {"ok": True, "member": member}

    @router.post(
        "/admin/api/members/{group_id}/{user_id}/analyze",
        dependencies=[Depends(require_admin)],
    )
    async def analyze_member(group_id: int, user_id: int) -> dict[str, Any]:
        try:
            member = await member_analysis.analyze(group_id, user_id, force=True)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            logger.exception(f"手动生成群员画像失败：group={group_id}, user={user_id}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"画像分析失败：{exc}",
            ) from exc
        return {"ok": True, "member": member}

    @router.delete(
        "/admin/api/members/{group_id}/{user_id}",
        dependencies=[Depends(require_admin)],
    )
    async def delete_member(group_id: int, user_id: int) -> dict[str, Any]:
        deleted = await member_analysis.store.delete_member(group_id, user_id)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="群员画像不存在")
        return {"ok": True}

    @router.post("/webhook/{token}", include_in_schema=False)
    async def receive_webhook(token: str, request: Request) -> dict[str, Any]:
        settings = config_store.snapshot()
        if (
            not settings.webhook_enabled
            or not settings.webhook_token
            or not hmac.compare_digest(token, settings.webhook_token)
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
        if settings.webhook_target_group <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="尚未配置转发目标群"
            )
        raw = await request.body()
        message = build_webhook_message(
            raw,
            request.headers.get("content-type", ""),
            settings.webhook_template,
            settings.webhook_prefix,
            settings.max_reply_chars,
        )
        if not message:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="没有可转发的内容")
        bot = next(iter(get_bots().values()), None)
        if bot is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="机器人未连接"
            )
        try:
            await bot.send_group_msg(group_id=settings.webhook_target_group, message=message)
        except Exception as exc:
            logger.warning(f"Webhook 转发失败：group={settings.webhook_target_group}, {exc}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=f"转发失败：{exc}"
            ) from exc
        logger.info(f"Webhook 已转发到群 {settings.webhook_target_group}")
        return {"ok": True, "group_id": settings.webhook_target_group}

    @router.get("/admin/api/join-gate", dependencies=[Depends(require_admin)])
    async def join_gate_status() -> dict[str, Any]:
        return join_gate.snapshot()

    @router.post("/admin/api/join-gate/refresh", dependencies=[Depends(require_admin)])
    async def join_gate_refresh() -> dict[str, Any]:
        try:
            snapshot = await join_gate.refresh()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=f"刷新失败：{exc}"
            ) from exc
        return {"ok": True, **snapshot}

    @router.get("/admin/api/summary", dependencies=[Depends(require_admin)])
    async def summary_latest(group_id: int) -> dict[str, Any]:
        latest = await daily_summary.store.get_latest_summary(group_id)
        if latest is None:
            return {"date": None, "html": "", "has_png": False}
        png_path = latest.get("png_path") or ""
        return {
            "date": latest["date"],
            "html": latest["html"],
            "has_png": bool(png_path) and Path(png_path).exists(),
        }

    @router.get("/admin/api/summary/png", dependencies=[Depends(require_admin)])
    async def summary_png(group_id: int) -> FileResponse:
        latest = await daily_summary.store.get_latest_summary(group_id)
        png_path = (latest or {}).get("png_path") or ""
        if not png_path or not Path(png_path).exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="暂无图片")
        return FileResponse(png_path, media_type="image/png", headers={"Cache-Control": "no-store"})

    @router.post("/admin/api/summary/generate", dependencies=[Depends(require_admin)])
    async def summary_generate(group_id: int, send: int = 0) -> dict[str, Any]:
        try:
            result = await daily_summary.generate(group_id, force=True)
        except ServiceError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        except Exception as exc:
            logger.exception("手动生成群聊总结失败")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=f"生成失败：{exc}"
            ) from exc
        sent = await send_summary_to_group(group_id, result) if send else False
        return {"ok": True, "date": result["date"], "has_png": bool(result.get("png")), "sent": sent}

    app.include_router(router)
    host = os.getenv("HOST", "127.0.0.1")
    port = os.getenv("PORT", "8080")
    logger.info(f"Web 管理后台已启用：http://{host}:{port}/admin")
