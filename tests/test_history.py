from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from xiaoke_bot.history import ChatHistoryStore


GROUP = "agent:qq-group-1:group:1"
OTHER = "agent:qq-group-2:group:2"


class ChatHistoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.store = ChatHistoryStore(Path(self._dir.name) / "chat_history.db")

    def tearDown(self) -> None:
        self._dir.cleanup()

    def add(self, session: str, role: str, content: str, keep: int = 40) -> None:
        asyncio.run(self.store.append(session, role, content, keep))

    def test_round_trip_preserves_order_oldest_first(self) -> None:
        self.add(GROUP, "user", "第一句")
        self.add(GROUP, "assistant", "回复")
        self.add(GROUP, "user", "第二句")
        loaded = self.store.load(40)
        self.assertEqual(
            loaded[GROUP],
            [
                {"role": "user", "content": "第一句"},
                {"role": "assistant", "content": "回复"},
                {"role": "user", "content": "第二句"},
            ],
        )

    def test_sessions_are_isolated(self) -> None:
        self.add(GROUP, "user", "群一")
        self.add(OTHER, "user", "群二")
        loaded = self.store.load(40)
        self.assertEqual([t["content"] for t in loaded[GROUP]], ["群一"])
        self.assertEqual([t["content"] for t in loaded[OTHER]], ["群二"])

    def test_retention_trims_per_session_not_globally(self) -> None:
        for i in range(8):
            self.add(GROUP, "user", f"g{i}", keep=3)
        for i in range(2):
            self.add(OTHER, "user", f"o{i}", keep=3)
        loaded = self.store.load(40)
        # the busy session is trimmed to its newest 3; the quiet one keeps both
        self.assertEqual([t["content"] for t in loaded[GROUP]], ["g5", "g6", "g7"])
        self.assertEqual([t["content"] for t in loaded[OTHER]], ["o0", "o1"])

    def test_load_honours_a_lowered_keep(self) -> None:
        for i in range(5):
            self.add(GROUP, "user", f"m{i}")
        self.assertEqual([t["content"] for t in self.store.load(2)[GROUP]], ["m3", "m4"])

    def test_load_zero_returns_nothing(self) -> None:
        self.add(GROUP, "user", "x")
        self.assertEqual(self.store.load(0), {})

    def test_empty_store_loads_empty(self) -> None:
        self.assertEqual(self.store.load(40), {})

    def test_clear_one_session_leaves_the_others(self) -> None:
        self.add(GROUP, "user", "a")
        self.add(OTHER, "user", "b")
        self.assertEqual(asyncio.run(self.store.clear(GROUP)), 1)
        loaded = self.store.load(40)
        self.assertNotIn(GROUP, loaded)
        self.assertIn(OTHER, loaded)

    def test_clear_everything(self) -> None:
        self.add(GROUP, "user", "a")
        self.add(OTHER, "user", "b")
        self.assertEqual(asyncio.run(self.store.clear()), 2)
        self.assertEqual(self.store.load(40), {})

    def test_reopening_the_same_file_sees_prior_turns(self) -> None:
        # This is the whole point: a restart must resume mid-conversation.
        self.add(GROUP, "user", "重启前说的话")
        reopened = ChatHistoryStore(self.store.path)
        self.assertEqual(
            [t["content"] for t in reopened.load(40)[GROUP]], ["重启前说的话"]
        )


    def test_delivery_metadata_survives_restart_without_guessing_legacy_channel(self) -> None:
        self.add(GROUP, "assistant", "旧回复")
        for delivery in ("voice", "voice_text", "text"):
            asyncio.run(self.store.append(GROUP, "assistant", "新回复", 40, {"delivery": delivery}))
        loaded = ChatHistoryStore(self.store.path).load(40)[GROUP]
        self.assertNotIn("delivery", loaded[0])
        self.assertEqual([item["delivery"] for item in loaded[1:]], ["voice", "voice_text", "text"])


if __name__ == "__main__":
    unittest.main()
