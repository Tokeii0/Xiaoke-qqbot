from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from test_runtime_config import BASE
from xiaoke_bot.clients import ServiceError
from xiaoke_bot.config import RuntimeConfigStore
from xiaoke_bot.judge import JevClient, JevVerdict, build_questions
from xiaoke_bot.request_log import RequestLogStore
from xiaoke_bot.web_admin import register_web_admin
from xiaoke_bot.web_search import SearchContext, WebSearchClient, clean_query, needs_search

SETTINGS = replace(BASE, search_enabled=True, search_api_key="private-tavily-secret", jev_enabled=True, jev_log_enabled=True)
VERDICT = JevVerdict(1, 1, 0, 0, 1, search_score=0.95)
SOURCE = {"title": "官方文档", "url": "https://example.test/docs", "content": "公开文档内容", "published_date": "2026-09-20"}


class WebSearchTests(unittest.IsolatedAsyncioTestCase):
    def test_search_gate_honors_threshold_switch_opt_out_and_explicit_fallback(self):
        self.assertTrue(needs_search("最新版本是什么", VERDICT, SETTINGS))
        self.assertFalse(needs_search("最新版本是什么", VERDICT, replace(SETTINGS, search_enabled=False)))
        for score in (0.74, float("nan"), float("inf"), 1.5):
            self.assertFalse(needs_search("最新版本是什么", replace(VERDICT, search_score=score), SETTINGS))
        self.assertTrue(needs_search("最新版本是什么", replace(VERDICT, search_score=0.75), SETTINGS))
        for query in ("不用联网，只解释一下", "不要搜索", "请离线回答"):
            self.assertFalse(needs_search(query, VERDICT, SETTINGS))
        self.assertTrue(needs_search("帮我搜一下 DeepSeek 最新模型", None, SETTINGS))
        self.assertFalse(needs_search("小可今天早餐吃什么", None, SETTINGS))

    def test_only_search_enabled_adds_jev_question_and_private_query_tokens_are_removed(self):
        self.assertIn("search_needed", build_questions(SETTINGS))
        self.assertNotIn("search_needed", build_questions(replace(SETTINGS, search_enabled=False)))
        query = clean_query("[QQ:123456789 昵称:私人成员] 新版本 private-tavily-secret user@example.com 13800138000", SETTINGS)
        self.assertEqual(query, "新版本")

    async def test_tavily_request_filters_unsafe_duplicate_results_and_logs_without_key(self):
        store = RequestLogStore()
        client = WebSearchClient(store)
        response = httpx.Response(200, json={"results": [SOURCE, SOURCE, {**SOURCE, "url": "javascript:alert(1)"},
            {**SOURCE, "url": "https://user:pass@example.test/x"}, {**SOURCE, "url": "https://example.test/second"}], "usage": {"credits": 1}})
        with patch("httpx.AsyncClient.post", AsyncMock(return_value=response)) as post:
            results = await client.search("DeepSeek 官方文档", SETTINGS)
        self.assertEqual(len(results), 2)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["search_depth"], "basic")
        self.assertFalse(payload["include_answer"])
        self.assertFalse(payload["include_raw_content"])
        self.assertNotIn("api_key", payload)
        detail = store.detail(store.recent()["items"][0]["id"])
        self.assertEqual(detail["kind"], "search")
        self.assertEqual(detail["feature"], "web_search")
        self.assertNotIn(SETTINGS.search_api_key, json.dumps(detail))
        self.assertEqual(detail["usage"], {"credits": 1})

    async def test_api_failures_are_honest_without_fabricated_sources(self):
        client = WebSearchClient()
        chat = SimpleNamespace(complete=AsyncMock())
        for response in (httpx.Response(401), httpx.Response(429), httpx.Response(302), httpx.Response(200, json={"wrong": []}), httpx.Response(200, text="bad json")):
            with patch("httpx.AsyncClient.post", AsyncMock(return_value=response)):
                context = await client.context(query="搜索 DeepSeek", recent=[], verdict=VERDICT, settings=SETTINGS, chat=chat)
            self.assertTrue(context.error)
            self.assertIn("没有成功", context.prompt())
            self.assertEqual(context.footer(), "")
        chat.complete.assert_not_awaited()

    async def test_missing_key_and_empty_result_are_distinct(self):
        client = WebSearchClient()
        chat = SimpleNamespace(complete=AsyncMock())
        with patch("httpx.AsyncClient.post", AsyncMock()) as post:
            context = await client.context(query="查一下最新版本", recent=[], verdict=VERDICT,
                settings=replace(SETTINGS, search_api_key=""), chat=chat)
        post.assert_not_awaited()
        self.assertIn("尚未配置", context.error)
        with patch("httpx.AsyncClient.post", AsyncMock(return_value=httpx.Response(200, json={"results": []}))):
            context = await client.context(query="查一下最新版本", recent=[], verdict=VERDICT, settings=SETTINGS, chat=chat)
        self.assertFalse(context.error)
        self.assertIn("没有找到", context.prompt())

    async def test_contextual_followup_rewrite_uses_only_bounded_public_conversation(self):
        client = WebSearchClient()
        chat = SimpleNamespace(complete=AsyncMock(return_value='{"query":"DeepSeek 最新 API 版本"}'))
        with patch.object(client, "search", AsyncMock(return_value=[SOURCE])) as search:
            context = await client.context(query="那它现在最新版本呢", recent=[{"text": "DeepSeek 的 API 123456789 private-tavily-secret"}],
                verdict=VERDICT, settings=SETTINGS, chat=chat)
        search.assert_awaited_once_with("DeepSeek 最新 API 版本", SETTINGS)
        messages = chat.complete.call_args.args[0]
        self.assertNotIn("123456789", str(messages))
        self.assertNotIn(SETTINGS.search_api_key, str(messages))
        self.assertEqual(chat.complete.call_args.kwargs["feature"], "search_query")
        self.assertIn("不可信", context.prompt())
        self.assertIn(SOURCE["url"], context.footer())

    async def test_rewrite_failure_uses_actual_user_query_not_invented_results(self):
        client = WebSearchClient()
        chat = SimpleNamespace(complete=AsyncMock(return_value="not json"))
        with patch.object(client, "search", AsyncMock(return_value=[SOURCE])) as search:
            context = await client.context(query="它的版本呢", recent=[{"text": "DeepSeek"}], verdict=VERDICT, settings=SETTINGS, chat=chat)
        self.assertEqual(search.call_args.args[0], "它的版本呢")
        self.assertEqual(context.results, [SOURCE])

    async def test_jev_search_score_is_carried_to_reply_flow(self):
        client, sdk = JevClient(), AsyncMock()
        sdk.system_one.return_value = SimpleNamespace(answers={"addressed": SimpleNamespace(noul=1), "worth": SimpleNamespace(noul=1),
            "offensive": SimpleNamespace(noul=0), "intrusion": SimpleNamespace(score=0, confidence=1), "search_needed": SimpleNamespace(noul=0.92)})
        with patch.object(client, "_ensure", AsyncMock(return_value=sdk)):
            verdict = await client.judge(current_text="查一下最新版本", current_speaker="测试", recent=[], settings=SETTINGS)
        self.assertEqual(verdict.search_score, 0.92)


class WebSearchAdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.config = RuntimeConfigStore(Path(folder.name) / "config.json", BASE)
        self.config.set_search_api_key("saved-search-secret")
        self.search = AsyncMock(spec=WebSearchClient)
        self.search.search.return_value = [SOURCE]
        app = FastAPI()
        environment = patch.dict(os.environ, {"WEB_ADMIN_ENABLED": "true", "WEB_ADMIN_TOKEN": "test-admin"})
        environment.start()
        self.addCleanup(environment.stop)
        with patch("xiaoke_bot.web_admin.get_app", return_value=app):
            register_web_admin(self.config, None, None, None, None, None, web_search_client=self.search)
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Authorization": "Bearer test-admin"})
        self.addAsyncCleanup(self.http.aclose)

    async def test_preview_auth_unsaved_key_and_no_store(self):
        self.assertEqual((await self.http.post("/admin/api/search/preview", json={"query":"test"}, headers={"Authorization":"Bearer wrong"})).status_code, 401)
        self.search.search.assert_not_awaited()
        response = await self.http.post("/admin/api/search/preview", json={"query":"DeepSeek 文档", "search_api_key":"preview-search-secret", "search_max_results":3})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("secret", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.search.search.call_args.args[1].search_api_key, "preview-search-secret")
        self.assertEqual(self.config.snapshot().search_api_key, "saved-search-secret")

    async def test_configuration_roundtrip_validates_bounds_and_keeps_keys_private(self):
        payload = self.config.public_dict()
        payload.update(search_enabled=True, search_api_key="new-search-secret", typo_enabled=True, typo_probability=0.06, typo_cooldown_minutes=40)
        response = await self.http.put("/admin/api/config", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("new-search-secret", response.text)
        self.assertTrue(response.json()["config"]["search_configured"])
        self.assertEqual(RuntimeConfigStore(self.config.path, BASE).snapshot().search_api_key, "new-search-secret")
        self.assertNotIn("new-search-secret", self.config.path.read_text(encoding="utf-8"))
        for field_name, value in (("typo_probability", 0.5), ("typo_cooldown_minutes", 0), ("search_max_results", 100), ("search_threshold", 2), ("search_api_base_url", "https://key:secret@example.test")):
            invalid = {**payload, field_name: value}
            self.assertEqual((await self.http.put("/admin/api/config", json=invalid)).status_code, 422)
