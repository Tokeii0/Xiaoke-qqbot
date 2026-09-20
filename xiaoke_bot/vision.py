from __future__ import annotations

from typing import Any, Iterable
import asyncio

# Segment types that are always QQ emoji / market stickers — never worth vision analysis.
_STICKER_TYPES = {"face", "mface", "marketface"}
# Substrings NapCat / go-cqhttp put in an image segment's `summary` for stickers.
_STICKER_SUMMARY_HINTS = ("表情", "贴纸", "sticker")


def is_sticker_segment(seg_type: str, data: dict[str, Any]) -> bool:
    """Best-effort 表情包/emoji detection across NapCat / go-cqhttp image fields."""
    if seg_type in _STICKER_TYPES:
        return True
    if seg_type != "image":
        return False
    sub_type = str(data.get("sub_type", data.get("subType", "")) or "").strip()
    if sub_type and sub_type != "0":
        return True
    summary = str(data.get("summary", "") or "")
    return any(hint in summary for hint in _STICKER_SUMMARY_HINTS)


def _image_ref(data: dict[str, Any]) -> str | None:
    ref = str(data.get("url") or data.get("file") or "").strip()
    return ref or None


def select_vision_images(
    segments: Iterable[Any],
    skip_stickers: bool,
    max_images: int,
) -> list[str]:
    """Pick real (non-sticker) image references from a message's segments, in order."""
    refs: list[str] = []
    for segment in segments:
        seg_type = str(getattr(segment, "type", "") or "")
        data = getattr(segment, "data", {}) or {}
        if seg_type != "image":
            continue
        if skip_stickers and is_sticker_segment(seg_type, data):
            continue
        ref = _image_ref(data)
        if ref and ref not in refs:
            refs.append(ref)
        if len(refs) >= max(1, max_images):
            break
    return refs


async def conversation_messages(entries, image_client, settings):
    """Serialize chat turns, embedding recent images on the turn that supplied them."""
    messages = [{"role":item["role"], "content":item.get("content", "")} for item in entries]
    if not settings.vision_enabled or settings.vision_mode != "direct":
        return messages
    remaining = settings.vision_context_images
    selected = []
    for index in range(len(entries) - 1, -1, -1):
        item = entries[index]
        refs = item.get("images", []) if item["role"] == "user" else []
        if not refs:
            continue
        chosen = refs[:remaining]
        remaining -= len(chosen)
        if chosen:
            selected.append((index, chosen))
            if len(chosen) < len(refs):
                messages[index]["content"] += f"\n[本条后 {len(refs)-len(chosen)} 张图片超出本轮图片上限，未提供]"
        else:
            messages[index]["content"] += "\n[较早的图片未附在本轮，请勿猜测图中内容]"
    # At most twelve images are fetched, across all turns, and only when a reply needs them.
    for index, refs in selected:
        parts = await asyncio.gather(*(image_client._image_part(ref) for ref in refs))
        content = messages[index]["content"]
        if any(part is None for part in parts):
            content += "\n[有图片已过期、过大或无法载入；无法看到时请说明并请对方重发，不猜测内容]"
        valid = [part for part in parts if part is not None]
        messages[index]["content"] = [{"type":"text", "text":content}, *valid] if valid else content
    return messages
