from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from test_runtime_config import BASE
from xiaoke_bot.humanize_state import (
    BotStateStore,
    ProactivityBudget,
    classify_interaction_event,
    decay_favorability,
    decay_mood,
    mood_label,
)


class _FakeConfigStore:
    def __init__(self, settings) -> None:
        self._settings = settings

    def snapshot(self):
        return self._settings


class _FakeGroupState:
    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    def is_enabled(self, group_id: int) -> bool:
        return self._enabled


class DecayTests(unittest.TestCase):
    def test_favorability_halves_distance_to_floor(self) -> None:
        # one half-life: distance to 50 halves; 100 -> 75, 0 -> 25
        self.assertAlmostEqual(decay_favorability(100, 30 * 86400, 30), 75.0, places=3)
        self.assertAlmostEqual(decay_favorability(0, 30 * 86400, 30), 25.0, places=3)

    def test_favorability_noop_and_clamp(self) -> None:
        self.assertEqual(decay_favorability(80, 0, 30), 80.0)
        self.assertLessEqual(decay_favorability(100, 999 * 86400, 30), 100.0)
        self.assertGreaterEqual(decay_favorability(0, 999 * 86400, 30), 0.0)

    def test_mood_decays_toward_zero(self) -> None:
        valence, arousal = decay_mood(1.0, 0.8, 6 * 3600, 6)
        self.assertAlmostEqual(valence, 0.5, places=3)
        self.assertAlmostEqual(arousal, 0.4, places=3)

    def test_mood_label_thresholds(self) -> None:
        self.assertEqual(mood_label(0.7, 0.6), "兴奋")
        self.assertEqual(mood_label(0.4, 0.3), "愉快")
        self.assertEqual(mood_label(-0.4, 0.2), "低落")
        self.assertEqual(mood_label(-0.6, 0.6), "烦躁")
        self.assertEqual(mood_label(0.0, 0.1), "平静")

    def test_classify_interaction_event(self) -> None:
        thanks = classify_interaction_event("谢谢你小可")
        assert thanks is not None
        self.assertGreater(thanks[0], 0)
        hostile = classify_interaction_event("你真是垃圾")
        assert hostile is not None
        self.assertLess(hostile[0], 0)
        self.assertIsNone(classify_interaction_event("今天天气不错"))


class BotStateStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_mood_round_trip_and_clamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = BotStateStore(Path(directory) / "state.db")
            fresh = await store.read_mood(20004, 6.0)
            self.assertEqual(fresh["label"], "平静")
            await store.nudge_mood(20004, 0.5, 0.3, 6.0)
            mood = await store.read_mood(20004, 6.0)
            self.assertGreater(mood["valence"], 0.4)
            # clamp: repeated big nudges stay within [-1, 1]
            for _ in range(10):
                await store.nudge_mood(20004, 0.5, 0.3, 6.0)
            capped = await store.read_mood(20004, 6.0)
            self.assertLessEqual(capped["valence"], 1.0)
            self.assertLessEqual(capped["arousal"], 1.0)

    async def test_proactive_ledger_counts_and_last(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = BotStateStore(Path(directory) / "state.db")
            self.assertEqual(
                await store.count_proactive(1, datetime(2000, 1, 1, tzinfo=timezone.utc)), 0
            )
            await store.record_proactive(1, "chime")
            await store.record_proactive(1, "greet")
            self.assertEqual(
                await store.count_proactive(1, datetime(2000, 1, 1, tzinfo=timezone.utc)), 2
            )
            self.assertEqual(
                await store.count_proactive(
                    1, datetime(2000, 1, 1, tzinfo=timezone.utc), kind="chime"
                ),
                1,
            )
            self.assertIsNotNone(await store.last_proactive_at(1, "chime"))
            self.assertIsNone(await store.last_proactive_at(1, "nope"))

    async def test_followup_ledger_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = BotStateStore(Path(directory) / "state.db")
            self.assertFalse(await store.recent_followup_exists(1, 2, "exam", 7))
            await store.record_followup(1, 2, "exam")
            self.assertTrue(await store.recent_followup_exists(1, 2, "exam", 7))
            # different topic / different user are not deduped
            self.assertFalse(await store.recent_followup_exists(1, 2, "trip", 7))
            self.assertFalse(await store.recent_followup_exists(1, 3, "exam", 7))


class ProactivityBudgetTests(unittest.IsolatedAsyncioTestCase):
    def _budget(self, store, *, enabled=True, group_enabled=True, **overrides):
        base = dict(
            proactive_enabled=enabled,
            context_timezone="UTC",
            proactive_quiet_start=0,
            proactive_quiet_end=0,
        )
        base.update(overrides)
        settings = replace(BASE, **base)
        return ProactivityBudget(
            _FakeConfigStore(settings), _FakeGroupState(group_enabled), store
        )

    async def test_master_switch_and_group_gates(self) -> None:
        gid = next(iter(BASE.allowed_groups))
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            store = BotStateStore(Path(directory) / "state.db")
            self.assertFalse(await self._budget(store, enabled=False).peek(gid, "chime", now))
            self.assertFalse(await self._budget(store).peek(9999, "chime", now))  # not allowed
            self.assertFalse(
                await self._budget(store, group_enabled=False).peek(gid, "chime", now)
            )
            self.assertTrue(await self._budget(store).peek(gid, "chime", now))

    async def test_quiet_hours_respected_and_bypassable(self) -> None:
        gid = next(iter(BASE.allowed_groups))
        now = datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            store = BotStateStore(Path(directory) / "state.db")
            budget = self._budget(store, proactive_quiet_start=23, proactive_quiet_end=8)
            self.assertFalse(await budget.peek(gid, "chime", now))
            self.assertTrue(await budget.peek(gid, "morning", now, ignore_quiet=True))

    async def test_hourly_cap_blocks(self) -> None:
        gid = next(iter(BASE.allowed_groups))
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            store = BotStateStore(Path(directory) / "state.db")
            budget = self._budget(store, proactive_hourly_cap=2, proactive_cooldown_seconds=0)
            self.assertTrue(await budget.peek(gid, "chime", now))
            await store.record_proactive(gid, "chime")
            await store.record_proactive(gid, "greet")
            self.assertFalse(await budget.peek(gid, "chime", now))


if __name__ == "__main__":
    unittest.main()
