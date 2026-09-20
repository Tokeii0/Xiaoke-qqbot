from __future__ import annotations

import base64
import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from test_runtime_config import BASE
from xiaoke_bot.campus_photo import CampusPhoto, CampusPhotoClient, photo_prompt
from xiaoke_bot.clients import ServiceError
from xiaoke_bot.judge import JevVerdict, build_questions
from xiaoke_bot.routine import routine_is_sleeping, routine_snapshot

SCHEDULE = "00:00 | 睡觉 | 睡觉\n09:00 | 运动 | 在操场慢跑\n12:00 | 吃饭 | 在食堂吃午饭\n18:00 | 娱乐 | 在宿舍玩游戏\n23:00 | 睡觉 | 睡觉"
SETTINGS = replace(BASE, routine_enabled=True, routine_variation="fixed", routine_photo_enabled=True,
    routine_photo_api_key="photo-test-secret", jev_enabled=True, routine_weekday_schedule=SCHEDULE, routine_weekend_schedule=SCHEDULE)
NOW = datetime(2026, 9, 21, 10)
VERDICT = JevVerdict(1, 1, 0, 0, 1, routine_reaction="share", routine_photo="sport")
JPEG = b"\xff\xd8\xff" + b"test-jpeg"


class CampusPhotoTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_ratio_medium_quality_and_first_person_prompt_reach_image_api(self):
        client = CampusPhotoClient()
        response = httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(JPEG).decode()}]})
        for ratio, size in (("4:3", "1024x768"), ("3:4", "768x1024")):
            settings = replace(SETTINGS, routine_photo_ratio=ratio)
            with patch("httpx.AsyncClient.post", AsyncMock(return_value=response)) as post:
                photo = await client.generate(settings, routine_snapshot(settings, NOW), "sport")
            payload = post.call_args.kwargs["json"]
            self.assertEqual(payload["size"], size)
            self.assertEqual(payload["quality"], "medium")
            self.assertEqual(payload["model"], "gpt-image-2.5-flare")
            self.assertEqual(payload["output_format"], "jpeg")
            self.assertEqual(payload["n"], 1)
            self.assertIn("第一人称", payload["prompt"])
            self.assertIn("不要自拍", payload["prompt"])
            self.assertIn("2026-09-21 星期一 10:00", payload["prompt"])
            self.assertIn("在操场慢跑", payload["prompt"])
            self.assertEqual(photo.size, size)
            self.assertEqual(photo.data, JPEG)

    async def test_failed_or_unsafe_image_responses_do_not_escape_as_photos(self):
        client = CampusPhotoClient()
        for response in (httpx.Response(401), httpx.Response(302), httpx.Response(200, json={"data": []}),
                         httpx.Response(200, json={"data": [{"b64_json": "invalid!"}]}),
                         httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"html").decode()}]})):
            with patch("httpx.AsyncClient.post", AsyncMock(return_value=response)), self.assertRaises(ServiceError):
                await client.generate(SETTINGS, routine_snapshot(SETTINGS, NOW), "sport")

    async def test_sleep_dorm_focus_or_jev_unavailable_never_generate(self):
        client = CampusPhotoClient()
        with patch.object(client, "generate", AsyncMock()) as generate:
            for settings, verdict, now in (
                (SETTINGS, VERDICT, NOW.replace(hour=2)), (SETTINGS, VERDICT, NOW.replace(hour=19)),
                (SETTINGS, replace(VERDICT, routine_reaction="focus"), NOW), (SETTINGS, None, NOW),
                (replace(SETTINGS, jev_enabled=False), VERDICT, NOW),
                (replace(SETTINGS, routine_enabled=False), VERDICT, NOW),
                (replace(SETTINGS, routine_photo_enabled=False), VERDICT, NOW),
                (SETTINGS, replace(VERDICT, routine_photo_score=0.6), NOW),
                (SETTINGS, replace(VERDICT, routine_photo_context=("2026-09-20", "09:00", "在校园散步")), NOW),
            ):
                send = AsyncMock()
                self.assertIsNone(await client.maybe_send(settings=settings, verdict=verdict, scope="g", send=send, now=now))
                send.assert_not_awaited()
            generate.assert_not_awaited()

    async def test_cooldown_duplicates_restart_and_cross_group_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "photo_state.json"
            client = CampusPhotoClient(path)
            photo = CampusPhoto(JPEG, "1024x768", "在操场慢跑", "sport", "2026-09-21 10:00")
            send = AsyncMock()
            with patch.object(client, "generate", AsyncMock(return_value=photo)) as generate:
                self.assertIsNotNone(await client.maybe_send(settings=SETTINGS, verdict=VERDICT, scope="one", send=send, now=NOW))
                self.assertIsNone(await client.maybe_send(settings=SETTINGS, verdict=VERDICT, scope="one", send=send, now=NOW + timedelta(minutes=40)))
                self.assertIsNotNone(await client.maybe_send(settings=SETTINGS, verdict=VERDICT, scope="two", send=send, now=NOW))
                generate.assert_awaited_once()
                self.assertEqual(send.await_count, 2)
            reloaded = CampusPhotoClient(path)
            with patch.object(reloaded, "generate", AsyncMock()) as generate:
                self.assertIsNone(await reloaded.maybe_send(settings=SETTINGS, verdict=VERDICT, scope="one", send=send, now=NOW + timedelta(minutes=55)))
                generate.assert_not_awaited()

    async def test_generation_and_delivery_failures_are_quiet_and_do_not_mark_sent(self):
        client = CampusPhotoClient()
        with patch.object(client, "generate", AsyncMock(side_effect=ServiceError("bad"))) as generate:
            send = AsyncMock()
            self.assertIsNone(await client.maybe_send(settings=SETTINGS, verdict=VERDICT, scope="one", send=send, now=NOW))
            self.assertIsNone(await client.maybe_send(settings=SETTINGS, verdict=VERDICT, scope="one", send=send, now=NOW + timedelta(seconds=10)))
            generate.assert_awaited_once()
            send.assert_not_awaited()
        self.assertNotIn("sent_at", client._sent["one"])
        photo = CampusPhoto(JPEG, "1024x768", "在操场慢跑", "sport", "2026-09-21 10:00")
        with patch.object(client, "generate", AsyncMock(return_value=photo)):
            self.assertIsNone(await client.maybe_send(settings=SETTINGS, verdict=VERDICT, scope="two",
                send=AsyncMock(side_effect=RuntimeError("QQ unavailable")), now=NOW))
        self.assertNotIn("event_id", client._sent["two"])

    async def test_activity_transition_during_generation_cancels_stale_photo(self):
        client = CampusPhotoClient()
        daytime = routine_snapshot(SETTINGS, NOW)
        nighttime = routine_snapshot(SETTINGS, NOW.replace(hour=23))
        photo = CampusPhoto(JPEG, "1024x768", "在操场慢跑", "sport", "2026-09-21 10:00")
        send = AsyncMock()
        with patch.object(client, "generate", AsyncMock(return_value=photo)), \
             patch("xiaoke_bot.campus_photo.routine_snapshot", side_effect=[daytime, daytime, nighttime]):
            self.assertIsNone(await client.maybe_send(settings=SETTINGS, verdict=VERDICT, scope="one", send=send, now=NOW))
        send.assert_not_awaited()

    async def test_past_dinner_reuses_exact_image_across_users_groups_restart_and_model_change(self):
        from test_routine_diary import DIARY_SETTINGS, EVENING
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "photos.json"
            client = CampusPhotoClient(path)
            dinner = next(row for row in client.diary.observe(DIARY_SETTINGS, EVENING) if row["label"] == "晚饭")
            verdict = replace(VERDICT, routine_photo="dining", routine_photo_event=dinner["event_id"],
                              routine_photo_context=("2026-09-21", "19:00", "在宿舍听歌"))
            async def generate(settings, snapshot, scene):
                self.assertEqual(snapshot["local_time"], "18:00")
                self.assertEqual(snapshot["current"]["activity"], dinner["details"])
                self.assertIn("傍晚", photo_prompt(settings, snapshot, scene))
                return CampusPhoto(JPEG, "768x1024", dinner["details"], scene, "2026-09-21 18:00")
            send = AsyncMock()
            with patch.object(client, "generate", AsyncMock(side_effect=generate)) as generator:
                one = await client.maybe_send(settings=DIARY_SETTINGS, verdict=verdict, scope="one", request_id="101", send=send, now=EVENING)
                two = await client.maybe_send(settings=DIARY_SETTINGS, verdict=verdict, scope="one", request_id="102", send=send, now=EVENING)
                self.assertEqual(one, two)
                self.assertIsNone(await client.maybe_send(settings=DIARY_SETTINGS, verdict=verdict, scope="one", request_id="101", send=send, now=EVENING))
                generator.assert_awaited_once()
            reloaded = CampusPhotoClient(path)
            with patch.object(reloaded, "generate", AsyncMock()) as generator:
                other = await reloaded.maybe_send(settings=replace(DIARY_SETTINGS, routine_photo_model="gpt-image-2.5-sunburst"),
                    verdict=verdict, scope="two", request_id="103", send=send, now=EVENING)
                self.assertEqual(one, other)
                self.assertEqual(other.event_id, dinner["event_id"])
                self.assertIsNone(await reloaded.maybe_send(settings=DIARY_SETTINGS, verdict=verdict, scope="one", request_id="102", send=send, now=EVENING))
                generator.assert_not_awaited()
            self.assertEqual(send.await_count, 3)
            self.assertTrue(all(call.args == (JPEG,) for call in send.await_args_list))

    async def test_concurrent_inquiries_generate_once_and_each_get_original(self):
        from test_routine_diary import DIARY_SETTINGS, EVENING
        client = CampusPhotoClient()
        dinner = next(row for row in client.diary.observe(DIARY_SETTINGS, EVENING) if row["label"] == "晚饭")
        verdict = replace(VERDICT, routine_photo="dining", routine_photo_event=dinner["event_id"])
        ready, proceed = asyncio.Event(), asyncio.Event()
        async def generate(*args):
            ready.set()
            await proceed.wait()
            return CampusPhoto(JPEG, "1024x768", dinner["details"], "dining", "2026-09-21 18:00")
        send = AsyncMock()
        with patch.object(client, "generate", AsyncMock(side_effect=generate)) as generator:
            first = asyncio.create_task(client.maybe_send(settings=DIARY_SETTINGS, verdict=verdict, scope="one", send=send, now=EVENING))
            await ready.wait()
            second = asyncio.create_task(client.maybe_send(settings=DIARY_SETTINGS, verdict=verdict, scope="two", send=send, now=EVENING))
            proceed.set()
            one, two = await asyncio.gather(first, second)
            self.assertEqual(one, two)
            generator.assert_awaited_once()
            self.assertEqual(send.await_count, 2)

    async def test_failed_delivery_keeps_original_and_retry_bypasses_generation_backoff(self):
        from test_routine_diary import DIARY_SETTINGS, EVENING
        client = CampusPhotoClient()
        dinner = next(row for row in client.diary.observe(DIARY_SETTINGS, EVENING) if row["label"] == "晚饭")
        verdict = replace(VERDICT, routine_photo="dining", routine_photo_event=dinner["event_id"])
        photo = CampusPhoto(JPEG, "1024x768", dinner["details"], "dining", "2026-09-21 18:00")
        with patch.object(client, "generate", AsyncMock(return_value=photo)) as generator:
            self.assertIsNone(await client.maybe_send(settings=DIARY_SETTINGS, verdict=verdict, scope="one", request_id="1",
                send=AsyncMock(side_effect=RuntimeError("offline")), now=EVENING))
            send = AsyncMock()
            result = await client.maybe_send(settings=DIARY_SETTINGS, verdict=verdict, scope="one", request_id="1", send=send, now=EVENING)
            self.assertEqual(result.data, JPEG)
            generator.assert_awaited_once()
            send.assert_awaited_once_with(JPEG)

    async def test_invalid_future_other_persona_and_sleep_targets_never_generate(self):
        from test_routine_diary import DIARY_SETTINGS, EVENING
        client = CampusPhotoClient()
        dinner = next(row for row in client.diary.observe(DIARY_SETTINGS, EVENING) if row["label"] == "晚饭")
        verdict = replace(VERDICT, routine_photo="dining", routine_photo_event=dinner["event_id"])
        with patch.object(client, "generate", AsyncMock()) as generator:
            for settings, choice, now in (
                (DIARY_SETTINGS, verdict, EVENING.replace(hour=10)),
                (DIARY_SETTINGS, verdict, EVENING.replace(hour=23)),
                (DIARY_SETTINGS, verdict, EVENING.replace(day=22)),
                (replace(DIARY_SETTINGS, bot_name="另一个人"), verdict, EVENING),
                (DIARY_SETTINGS, replace(verdict, routine_photo_event="unknown"), EVENING),
                (DIARY_SETTINGS, replace(verdict, routine_photo_event="none"), EVENING),
            ):
                self.assertIsNone(await client.maybe_send(settings=settings, verdict=choice, scope="one", send=AsyncMock(), now=now))
            generator.assert_not_awaited()

    async def test_photo_storage_failure_never_breaks_an_already_delivered_reply(self):
        client = CampusPhotoClient()
        with patch.object(client.diary, "observe", side_effect=OSError("disk unavailable")), \
             patch.object(client, "generate", AsyncMock()) as generator:
            send = AsyncMock()
            self.assertIsNone(await client.maybe_send(settings=SETTINGS, verdict=VERDICT, scope="one", send=send, now=NOW))
            generator.assert_not_awaited()
            send.assert_not_awaited()

    async def test_requested_past_image_survives_activity_change_but_not_sleep(self):
        from test_routine_diary import DIARY_SETTINGS, EVENING
        for later_hour, delivers in ((22, True), (23, False)):
            client = CampusPhotoClient()
            dinner = next(row for row in client.diary.observe(DIARY_SETTINGS, EVENING) if row["label"] == "晚饭")
            verdict = replace(VERDICT, routine_photo="dining", routine_photo_event=dinner["event_id"])
            initial = routine_snapshot(DIARY_SETTINGS, EVENING)
            later = routine_snapshot(DIARY_SETTINGS, EVENING.replace(hour=later_hour))
            if delivers:
                later["current"] = {**later["current"], "activity": "去校园小路走走"}
            photo = CampusPhoto(JPEG, "1024x768", dinner["details"], "dining", "2026-09-21 18:00")
            with patch.object(client, "generate", AsyncMock(return_value=photo)), \
                 patch("xiaoke_bot.campus_photo.routine_snapshot", side_effect=[initial, initial, later]):
                send = AsyncMock()
                result = await client.maybe_send(settings=DIARY_SETTINGS, verdict=verdict, scope="one", send=send, now=EVENING)
                self.assertEqual(result is not None, delivers)
                self.assertEqual(send.await_count, int(delivers))

    def test_sleep_gate_day_night_lighting_and_jev_question(self):
        self.assertTrue(routine_is_sleeping(SETTINGS, NOW.replace(hour=2)))
        self.assertFalse(routine_is_sleeping(SETTINGS, NOW))
        self.assertFalse(routine_is_sleeping(replace(SETTINGS, routine_sleep_silent=False), NOW.replace(hour=2)))
        self.assertIn("上午", photo_prompt(SETTINGS, routine_snapshot(SETTINGS, NOW), "campus"))
        self.assertIn("深夜", photo_prompt(SETTINGS, routine_snapshot(SETTINGS, NOW.replace(hour=22)), "campus"))
        self.assertIn("routine_photo", build_questions(SETTINGS))
        self.assertNotIn("routine_photo", build_questions(replace(SETTINGS, routine_photo_enabled=False)))

    def test_key_reuse_is_limited_to_identical_provider_addresses(self):
        same = replace(BASE, voice_api_base_url="https://api.openai.com/v1", voice_api_key="voice-secret")
        self.assertEqual(same.routine_photo_key, "voice-secret")
        self.assertEqual(replace(same, routine_photo_api_base_url="https://other.example/v1").routine_photo_key, "")
        self.assertEqual(replace(same, routine_photo_api_key="own").routine_photo_key, "own")


if __name__ == "__main__":
    unittest.main()
