from __future__ import annotations

import base64
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from test_runtime_config import BASE
from xiaoke_bot.clients import ChatClient, VisionClient
from xiaoke_bot.history import ChatHistoryStore
from xiaoke_bot.vision import conversation_messages

SETTINGS=replace(BASE,vision_enabled=True,vision_mode="direct",vision_context_images=2)
PNG=b"\x89PNG\r\n\x1a\n"+b"synthetic transport fixture"
IMAGE={"type":"image_url","image_url":{"url":"data:image/png;base64,"+base64.b64encode(PNG).decode()}}


class MultimodalTests(unittest.IsolatedAsyncioTestCase):
    async def test_images_remain_with_original_turn_and_only_newest_fit_budget(self):
        client=SimpleNamespace(_image_part=AsyncMock(return_value=IMAGE))
        entries=[{"role":"user","content":"第一张","images":["old"],"user_id":7},
                 {"role":"user","content":"第二张","images":["new1","new2"]},
                 {"role":"user","content":"刚才第二张哪里不对？"}]
        result=await conversation_messages(entries,client,SETTINGS)
        self.assertIn("未附在本轮",result[0]["content"])
        self.assertEqual(result[1]["content"],[{"type":"text","text":"第二张"},IMAGE,IMAGE])
        self.assertEqual(result[2],entries[2])
        self.assertNotIn("user_id",result[0])
        self.assertEqual([call.args[0] for call in client._image_part.call_args_list],["new1","new2"])
        self.assertEqual(entries[0]["content"],"第一张")

    async def test_unavailable_image_is_explicit_and_disabled_mode_does_not_fetch(self):
        client=SimpleNamespace(_image_part=AsyncMock(return_value=None))
        entries=[{"role":"user","content":"这张图是什么","images":["expired"]}]
        result=await conversation_messages(entries,client,SETTINGS)
        self.assertIn("无法载入",result[0]["content"])
        client._image_part.reset_mock()
        self.assertEqual(await conversation_messages(entries,client,replace(SETTINGS,vision_enabled=False)),[{"role":"user","content":"这张图是什么"}])
        client._image_part.assert_not_awaited()

    async def test_history_migration_and_media_metadata_survive_restart_and_remain_scoped(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"old.db"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE chat_history(id INTEGER PRIMARY KEY AUTOINCREMENT,session_key TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,created_at TEXT NOT NULL)")
                db.execute("INSERT INTO chat_history(session_key,role,content,created_at) VALUES('group1','user','旧消息','2026-01-01T00:00:00+00:00')")
            db.close()
            store=ChatHistoryStore(path)
            metadata={"images":["https://example.test/image.png"],"message_id":"42","user_id":7,"created_at":"2026-09-19T01:00:00+00:00"}
            await store.append("group1","user","[图片]",40,metadata)
            await store.append("group2","user","别的群",40)
            loaded=ChatHistoryStore(path).load(40)
            self.assertEqual(loaded["group1"][0],{"role":"user","content":"旧消息"})
            self.assertEqual(loaded["group1"][1]["images"],metadata["images"])
            self.assertNotIn("images",loaded["group2"][0])

    async def test_image_download_validates_bytes_caches_and_rejects_oversize(self):
        count=0
        def handler(request):
            nonlocal count
            count+=1
            if request.url.path == "/large":
                return httpx.Response(200,headers={"content-length":str(VisionClient.MAX_IMAGE_BYTES+1)})
            if request.url.path == "/html":
                return httpx.Response(200,content=b"<html>not an image</html>")
            return httpx.Response(200,content=PNG)
        client=VisionClient()
        await client.http.aclose()
        client.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.close)
        self.assertEqual(await client._image_part("https://example.test/image"),IMAGE)
        self.assertEqual(await client._image_part("https://example.test/image"),IMAGE)
        self.assertEqual(count,1)
        self.assertIsNone(await client._image_part("https://example.test/large"))
        self.assertIsNone(await client._image_part("https://example.test/html"))
        self.assertIsNone(await client._image_part("file:///C:/private.png"))
        self.assertIsNone(await client._image_part("base64://invalid"))

    async def test_multimodal_content_is_sent_unchanged_to_chat_endpoint(self):
        requests=[]
        def handler(request):
            requests.append(request)
            return httpx.Response(200,json={"choices":[{"message":{"content":"红色"}}]})
        client=ChatClient()
        await client.http.aclose()
        client.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.close)
        messages=[{"role":"user","content":[{"type":"text","text":"什么颜色"},IMAGE]}]
        self.assertEqual(await client.complete(messages,replace(SETTINGS,api_key="test")),"红色")
        import json
        self.assertEqual(json.loads(requests[0].content)["messages"],messages)


if __name__ == "__main__":
    unittest.main()
