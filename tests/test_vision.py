from __future__ import annotations

import unittest

from xiaoke_bot.vision import is_sticker_segment, select_vision_images


class _Seg:
    def __init__(self, seg_type: str, data: dict) -> None:
        self.type = seg_type
        self.data = data


class VisionHelperTests(unittest.TestCase):
    def test_is_sticker_segment(self) -> None:
        self.assertTrue(is_sticker_segment("face", {"id": "1"}))
        self.assertTrue(is_sticker_segment("mface", {}))
        self.assertTrue(is_sticker_segment("image", {"sub_type": "1"}))
        self.assertTrue(is_sticker_segment("image", {"subType": 1}))
        self.assertTrue(is_sticker_segment("image", {"summary": "[动画表情]"}))
        self.assertFalse(is_sticker_segment("image", {"sub_type": "0", "summary": "[图片]"}))
        self.assertFalse(is_sticker_segment("image", {}))
        self.assertFalse(is_sticker_segment("text", {}))

    def test_select_skips_stickers_dedups_and_prefers_url(self) -> None:
        segments = [
            _Seg("text", {}),
            _Seg("face", {"id": "1"}),
            _Seg("image", {"url": "http://x/a.jpg", "sub_type": "0"}),
            _Seg("image", {"url": "http://x/meme.gif", "sub_type": "1"}),  # sticker image
            _Seg("image", {"url": "http://x/a.jpg"}),  # duplicate
            _Seg("image", {"url": "http://x/b.png"}),
            _Seg("image", {"file": "c.jpg"}),
        ]
        refs = select_vision_images(segments, skip_stickers=True, max_images=5)
        self.assertEqual(refs, ["http://x/a.jpg", "http://x/b.png", "c.jpg"])

    def test_select_can_keep_stickers_and_respects_limit(self) -> None:
        segments = [
            _Seg("image", {"url": "http://x/1.jpg", "sub_type": "1"}),
            _Seg("image", {"url": "http://x/2.jpg", "sub_type": "1"}),
            _Seg("image", {"url": "http://x/3.jpg", "sub_type": "1"}),
        ]
        self.assertEqual(
            select_vision_images(segments, skip_stickers=False, max_images=2),
            ["http://x/1.jpg", "http://x/2.jpg"],
        )
        self.assertEqual(select_vision_images(segments, skip_stickers=True, max_images=5), [])


if __name__ == "__main__":
    unittest.main()
