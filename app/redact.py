"""敏感词脱敏：修订稿精确命中、合并遮蔽，并给出音频静音毫秒区间。

处理流程：
1. 在修订稿中查找所有敏感词的**逐字精确出现**；
2. 把重叠或相接（端点相邻）的命中合并为半开字符区间，区间内字符用等长的 █ 遮蔽；
3. 每个合并区间附带原文及首字 start、末字 end；
4. 各区间的时间向两侧扩展 padding_ms、裁剪到录音边界 [0, total_duration]，
   再合并重叠或相接的时间区间，得到供音频静音处理的半开毫秒区间。
"""
from __future__ import annotations

from .errors import InvalidTimelineError
from .schemas import (
    MuteRange,
    RedactedSpan,
    RedactRequest,
    RedactResponse,
    RevisedCharTimeline,
)

MASK_CHAR = "█"


def validate_sensitive_words(req: RedactRequest) -> None:
    """校验敏感词列表：每个非空且互不重复。

    前置条件：请求已通过 Pydantic 校验（敏感词 1～50 个、每个非空）。
    """
    seen: set[str] = set()
    for word in req.sensitive_words:
        if word in seen:
            raise InvalidTimelineError(
                f"敏感词必须互不重复：{word!r} 重复出现")
        seen.add(word)


def find_word_intervals(text: str, words: list[str]) -> list[tuple[int, int]]:
    """查找所有敏感词在 text 中的精确出现，合并重叠或相接的半开字符区间。

    返回按起始下标升序、互不重叠（端点相接也已合并）的 ``(start, end)`` 列表。
    """
    n = len(text)
    hits: list[tuple[int, int]] = []
    for word in words:
        length = len(word)
        if length == 0 or length > n:
            continue
        pos = text.find(word)
        while pos != -1:
            hits.append((pos, pos + length))
            pos = text.find(word, pos + 1)

    hits.sort()
    merged: list[tuple[int, int]] = []
    for start, end in hits:
        if merged and start <= merged[-1][1]:
            # 重叠或相接（半开区间端点相等）：向后扩展。
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def _merge_time_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """合并重叠或相接的毫秒半开区间；调用前先按起始时间排序。"""
    merged: list[tuple[int, int]] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def build_redaction(text: str, chars: list[RevisedCharTimeline],
                    words: list[str], padding_ms: int,
                    total_duration: int) -> RedactResponse:
    """生成脱敏文本、字符区间与扩展合并后的音频静音区间。"""
    intervals = find_word_intervals(text, words)

    masked = list(text)
    spans: list[RedactedSpan] = []
    expanded: list[tuple[int, int]] = []
    for start, end in intervals:
        for i in range(start, end):
            masked[i] = MASK_CHAR
        first_char = chars[start]
        last_char = chars[end - 1]
        span_start = first_char.start
        span_end = last_char.end
        spans.append(RedactedSpan(
            start_index=start,
            end_index=end,
            text=text[start:end],
            start=span_start,
            end=span_end,
        ))
        # 向两侧扩展 padding_ms 并裁剪到录音边界。
        expanded.append((
            max(0, span_start - padding_ms),
            min(total_duration, span_end + padding_ms),
        ))

    expanded.sort()
    audio_ranges = [
        MuteRange(start=start, end=end)
        for start, end in _merge_time_ranges(expanded)
    ]

    return RedactResponse(
        redacted_text="".join(masked),
        spans=spans,
        audio_ranges=audio_ranges,
    )
