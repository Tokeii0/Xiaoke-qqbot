from __future__ import annotations

import asyncio
import math
import random
import re

from .config import Settings


_CODE_FENCE = re.compile(r"(```[\s\S]*?```)")
_URL = re.compile(r"(https?://[^\s]+)", flags=re.IGNORECASE)
_SENTENCE_PUNCTUATION = re.compile(r"[，。！？；：、,.!?;:]+")
_OPEN_BRACKETS = "([{（［｛「『【〖〔《〈〘＜"
_CLOSE_BRACKETS = ")]}）］｝」』】〗〕》〉〙＞"


def _inside_bracket(text: str) -> bool:
    """True if `text` has an unclosed opening bracket — i.e. we are mid颜文字/kaomoji."""
    depth = 0
    for char in text:
        if char in _OPEN_BRACKETS:
            depth += 1
        elif char in _CLOSE_BRACKETS:
            depth = max(0, depth - 1)
    return depth > 0


def should_probability_reply(enabled: bool, probability: float, sample: float | None = None) -> bool:
    if not enabled or probability <= 0:
        return False
    value = random.random() if sample is None else sample
    return value < min(1.0, probability)


def should_quote_reply(settings: Settings, sample: float | None = None) -> bool:
    return should_probability_reply(
        settings.quote_reply_enabled,
        settings.quote_reply_probability,
        sample,
    )


def _humanize_plain_text(text: str, remove_punctuation: bool, newline_to_space: bool) -> str:
    pieces = _URL.split(text)
    for index in range(0, len(pieces), 2):
        value = pieces[index]
        if remove_punctuation:
            value = _SENTENCE_PUNCTUATION.sub("", value)
        if newline_to_space:
            value = re.sub(r"\s*\n\s*", " ", value)
            value = re.sub(r" {2,}", " ", value)
        pieces[index] = value
    return "".join(pieces)


def humanize_reply(content: str, settings: Settings) -> str:
    if not (settings.humanize_remove_punctuation or settings.humanize_newline_to_space):
        return content.strip()
    segments = _CODE_FENCE.split(content)
    for index in range(0, len(segments), 2):
        segments[index] = _humanize_plain_text(
            segments[index],
            settings.humanize_remove_punctuation,
            settings.humanize_newline_to_space,
        )
    result = "".join(segments).strip()
    return result or content.strip()


def split_reply_segments(
    content: str,
    settings: Settings,
    samples: list[float] | None = None,
) -> list[str]:
    """Probabilistically split a reply at spaces without creating message storms."""
    if not settings.segment_send_enabled or "```" in content:
        return [content]
    words = re.split(r"[ \t]+", content.strip())
    if len(words) < 2:
        return [content]
    sample_iter = iter(samples) if samples is not None else None
    parts = [words[0]]
    for word in words[1:]:
        sample = next(sample_iter, 1.0) if sample_iter is not None else random.random()
        # Never start a new segment while inside an unclosed bracket run: this keeps
        # 颜文字/kaomoji like "( ﾟ 口 ﾟ)" whole instead of splitting them across messages.
        if (
            not _inside_bracket(parts[-1])
            and len(parts) < settings.segment_max_parts
            and sample < settings.segment_probability
        ):
            parts.append(word)
        else:
            parts[-1] += " " + word
    return [part.strip() for part in parts if part.strip()] or [content]


async def apply_reply_delay(settings: Settings) -> None:
    if not settings.humanize_delay_enabled:
        return
    low = min(settings.humanize_delay_min, settings.humanize_delay_max)
    high = max(settings.humanize_delay_min, settings.humanize_delay_max)
    if high > 0:
        await asyncio.sleep(random.uniform(low, high))


def segment_delay(text: str, settings: Settings, *, sample: float = 0.5) -> float:
    """Smooth typing cadence: short phrases stay quick, long chunks approach a ceiling."""
    low = min(settings.segment_delay_min, settings.segment_delay_max)
    high = max(settings.segment_delay_min, settings.segment_delay_max)
    if high <= 0:
        return 0.0
    length = len(re.sub(r"\s+", "", text))
    base = low + (high - low) * sample
    return min(10.0, max(0.7, base + 6.5 * (1 - math.exp(-length / 55))))


async def apply_segment_delay(settings: Settings, text: str = "") -> None:
    delay = segment_delay(text, settings, sample=random.random())
    if delay > 0:
        await asyncio.sleep(delay)
