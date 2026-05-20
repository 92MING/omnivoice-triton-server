from __future__ import annotations

import math
import logging
import warnings
from typing import TypedDict

import regex as re

warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API")
import jieba  # noqa: E402

jieba.setLogLevel(logging.ERROR)

COMMON_PUNCTUATIONS = (
    " \t\r\n"
    ".,!?;:，。！？；：、"
    "\"'“”‘’`"
    "()[]{}<>（）【】《》「」『』"
    "/\\|+-=*#@$%^&~"
)

EMOJI_RE_PATTERN = re.compile(r"\p{Extended_Pictographic}", re.UNICODE)

_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?", re.UNICODE)
_SPLIT_PAT = re.compile(
    r"|".join(re.escape(c) for c in COMMON_PUNCTUATIONS) + r"|[\s]+",
    re.UNICODE,
)
_CJK_PAT = re.compile(
    r"[\u1100-\u11FF"
    r"\u2E80-\u2EFF"
    r"\u3000-\u303F"
    r"\u3040-\u30FF"
    r"\u3100-\u318F"
    r"\u3400-\u4DBF"
    r"\u4E00-\u9FFF"
    r"\uA960-\uA97F"
    r"\uAC00-\uD7FF"
    r"\uF900-\uFAFF"
    r"\uFE30-\uFE4F"
    r"\u0E00-\u0E7F"
    r"]+",
    re.UNICODE,
)

_DEFAULT_SPLIT_PATTERNS = (
    re.compile(r"(?:\r?\n\s*){2,}", re.UNICODE),
    re.compile(
        r"(?:\r?\n)(?=(?:#{1,6}\s|```|~~~|\|.+\||"
        r"<(?:div|section|article|aside|nav|main|p|ul|ol|li|table|tr|td|th|pre|code|h[1-6])\b))",
        re.UNICODE,
    ),
    re.compile(r"(?:\r?\n)+", re.UNICODE),
    re.compile(r"(?:[。！？!?]+|…{1,2}|(?<!\d)\.(?!\d))(?:[\"'”’」』）\]]*\s*)", re.UNICODE),
    re.compile(r"[；;：:]+(?:\s*)", re.UNICODE),
    re.compile(r"[，,、]+(?:\s*)", re.UNICODE),
    re.compile(r"\s+", re.UNICODE),
)
_HARD_BOUNDARY_PATTERN_COUNT = 3
_SENTENCE_PATTERN_INDEX = 3

_CJK_BLOCK_PAT = re.compile(
    r"[\u3400-\u4DBF\u4E00-\u9FFF\u3040-\u30FF\u31F0-\u31FF"
    r"\uAC00-\uD7AF\u1100-\u11FF\u0E00-\u0E7F]+",
    re.UNICODE,
)
_NON_CJK_TOKEN_PAT = re.compile(
    r"</?[A-Za-z][^>\n]*?>"
    r"|```+"
    r"|~~~+"
    r"|[A-Za-z_][A-Za-z0-9_./:-]*"
    r"|\d+(?:\.\d+)?"
    r"|\|+"
    r"|[{}\[\]()<>]+"
    r"|[:;,=+\-*/\\@#$%^&!?~]+"
    r"|[^\s]",
    re.UNICODE,
)


class TextChunk(TypedDict):
    text: str
    offset: int
    word_count: int


class _SemanticUnit(TypedDict):
    start: int
    end: int
    word_count: int


def word_count(
    text: str,
    count_number: bool = True,
    count_emoji: bool = True,
) -> int:
    """Count mixed-language text using CJK character blocks and non-CJK tokens."""
    if not text:
        return 0

    count = 0

    if count_number:
        count += len(_NUMBER_PATTERN.findall(text))
        text = _NUMBER_PATTERN.sub(" ", text)

    if count_emoji:
        count += len(EMOJI_RE_PATTERN.findall(text))
        text = EMOJI_RE_PATTERN.sub(" ", text)

    last_end = 0
    for match in _CJK_PAT.finditer(text):
        start, end = match.span()
        non_cjk = text[last_end:start]
        if non_cjk.strip():
            tokens = [token for token in _SPLIT_PAT.split(non_cjk) if token.strip()]
            count += len(tokens)

        cjk_clean = re.sub(r"\s+", "", match.group())
        count += len(cjk_clean)
        last_end = end

    remaining = text[last_end:]
    if remaining.strip():
        tokens = [token for token in _SPLIT_PAT.split(remaining) if token.strip()]
        count += len(tokens)

    return count


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _span_word_count(text: str, start: int, end: int) -> int:
    start, end = _trim_span(text, start, end)
    if start >= end:
        return 0
    return word_count(text[start:end])


def _make_chunk(text: str, start: int, end: int) -> TextChunk | None:
    start, end = _trim_span(text, start, end)
    if start >= end:
        return None
    chunk_text = text[start:end]
    return {
        "text": chunk_text,
        "offset": start,
        "word_count": word_count(chunk_text),
    }


def _allowed_word_count(max_word_count: int, soft_overflow_ratio: float) -> int:
    return max(max_word_count, math.ceil(max_word_count * (1.0 + soft_overflow_ratio)))


def _split_span_by_pattern(
    text: str,
    start: int,
    end: int,
    pattern: re.Pattern[str],
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = start
    for match in pattern.finditer(text, start, end):
        split_end = match.end()
        if split_end <= cursor:
            continue
        trimmed = _trim_span(text, cursor, split_end)
        if trimmed[0] < trimmed[1]:
            spans.append(trimmed)
        cursor = split_end

    if cursor < end:
        trimmed = _trim_span(text, cursor, end)
        if trimmed[0] < trimmed[1]:
            spans.append(trimmed)
    return spans


def _pack_spans_balanced(
    text: str,
    spans: list[tuple[int, int]],
    max_word_count: int,
    soft_overflow_ratio: float,
) -> list[tuple[int, int]]:
    if not spans:
        return []

    allowed_word_count = _allowed_word_count(max_word_count, soft_overflow_ratio)
    span_word_counts = [_span_word_count(text, start, end) for start, end in spans]
    packed: list[tuple[int, int]] = []
    index = 0

    while index < len(spans):
        remaining_word_count = sum(span_word_counts[index:])
        if remaining_word_count <= 0:
            packed.extend(spans[index:])
            break

        remaining_chunk_count = max(1, math.ceil(remaining_word_count / max_word_count))
        target_word_count = min(
            max_word_count,
            max(1, math.ceil(remaining_word_count / remaining_chunk_count)),
        )

        current_word_count = 0
        best_end_index = index
        best_word_count = span_word_counts[index]
        best_score = abs(best_word_count - target_word_count)

        for cursor in range(index, len(spans)):
            next_word_count = current_word_count + span_word_counts[cursor]
            if cursor > index and next_word_count > allowed_word_count:
                break

            current_word_count = next_word_count
            current_score = abs(current_word_count - target_word_count)
            if current_score < best_score or (
                current_score == best_score and current_word_count > best_word_count
            ):
                best_end_index = cursor
                best_word_count = current_word_count
                best_score = current_score

            if current_word_count >= target_word_count and current_score == 0:
                break

        packed.append((spans[index][0], spans[best_end_index][1]))
        index = best_end_index + 1

    return packed


def _pack_sentence_groups(
    text: str,
    groups: list[tuple[list[tuple[int, int]], bool]],
    max_word_count: int,
    soft_overflow_ratio: float,
    same_sentence_penalty: int,
    sentence_boundary_penalty: int,
    fragment_boundary_penalty: int,
    short_underfill_ratio: float,
    short_underfill_penalty: int,
) -> list[tuple[int, int]]:
    """Pack sentence-level groups with length and boundary-quality scoring."""
    items: list[tuple[int, int, int, bool, int]] = []
    for group_index, (spans, is_atomic) in enumerate(groups):
        for start, end in spans:
            wc = _span_word_count(text, start, end)
            if wc > 0:
                items.append((start, end, wc, is_atomic, group_index))
    if not items:
        return []

    allowed_word_count = _allowed_word_count(max_word_count, soft_overflow_ratio)
    packed: list[tuple[int, int]] = []
    index = 0

    def boundary_penalty(left: tuple[int, int, int, bool, int], right: tuple[int, int, int, bool, int]) -> int:
        _, _, _, left_atomic, left_group = left
        _, _, _, right_atomic, right_group = right
        if left_group == right_group:
            return same_sentence_penalty
        if left_atomic and right_atomic:
            return sentence_boundary_penalty
        return fragment_boundary_penalty

    while index < len(items):
        remaining_word_count = sum(item[2] for item in items[index:])
        remaining_chunk_count = max(1, math.ceil(remaining_word_count / max_word_count))
        target_word_count = min(
            max_word_count,
            max(1, math.ceil(remaining_word_count / remaining_chunk_count)),
        )

        current_word_count = 0
        current_penalty = 0
        best_end_index = index
        best_word_count = items[index][2]
        best_score = abs(best_word_count - target_word_count)

        for cursor in range(index, len(items)):
            next_word_count = current_word_count + items[cursor][2]
            if cursor > index and next_word_count > allowed_word_count:
                break

            if cursor > index:
                current_penalty += boundary_penalty(items[cursor - 1], items[cursor])
            current_word_count = next_word_count

            current_score = abs(current_word_count - target_word_count) + current_penalty
            short_underfill_words = math.floor(max_word_count * short_underfill_ratio)
            if current_word_count < short_underfill_words:
                current_score += (
                    short_underfill_words - current_word_count
                ) * short_underfill_penalty

            if current_score < best_score or (
                current_score == best_score and current_word_count > best_word_count
            ):
                best_end_index = cursor
                best_word_count = current_word_count
                best_score = current_score

        packed.append((items[index][0], items[best_end_index][1]))
        index = best_end_index + 1

    return packed


def _iter_semantic_units(text: str, start: int, end: int) -> list[_SemanticUnit]:
    units: list[_SemanticUnit] = []
    cursor = start

    while cursor < end:
        cjk_match = _CJK_BLOCK_PAT.search(text, cursor, end)
        block_start = cjk_match.start() if cjk_match else end

        if cursor < block_start:
            for match in _NON_CJK_TOKEN_PAT.finditer(text, cursor, block_start):
                token_count = word_count(match.group())
                if token_count > 0:
                    token_start, token_end = match.span()
                    units.append(
                        {"start": token_start, "end": token_end, "word_count": token_count}
                    )

        if not cjk_match:
            break

        seg_start, seg_end = cjk_match.span()
        segment = text[seg_start:seg_end]
        has_token = False
        try:
            for _, rel_start, rel_end in jieba.tokenize(segment, mode="default"):
                token_text = segment[rel_start:rel_end]
                token_count = word_count(token_text)
                if token_count <= 0:
                    continue
                has_token = True
                units.append(
                    {
                        "start": seg_start + rel_start,
                        "end": seg_start + rel_end,
                        "word_count": token_count,
                    }
                )
        except Exception:
            has_token = False

        if not has_token:
            for idx in range(seg_start, seg_end):
                units.append(
                    {
                        "start": idx,
                        "end": idx + 1,
                        "word_count": word_count(text[idx : idx + 1]) or 1,
                    }
                )

        cursor = seg_end

    return units


def _hard_split_span(
    text: str,
    start: int,
    end: int,
    max_word_count: int,
    soft_overflow_ratio: float,
) -> list[tuple[int, int]]:
    units = _iter_semantic_units(text, start, end)
    if not units:
        trimmed = _trim_span(text, start, end)
        return [trimmed] if trimmed[0] < trimmed[1] else []

    total_word_count = sum(unit["word_count"] for unit in units)
    allowed_word_count = _allowed_word_count(max_word_count, soft_overflow_ratio)
    chunk_count = max(1, math.ceil(total_word_count / max_word_count))
    target_word_count = min(max_word_count, max(1, math.ceil(total_word_count / chunk_count)))
    spans: list[tuple[int, int]] = []
    unit_index = 0

    while unit_index < len(units):
        remaining_word_count = sum(unit["word_count"] for unit in units[unit_index:])
        remaining_chunk_count = max(1, math.ceil(remaining_word_count / max_word_count))
        dynamic_target = min(
            max_word_count,
            max(target_word_count, math.ceil(remaining_word_count / remaining_chunk_count)),
        )

        current_word_count = 0
        best_index = unit_index
        best_word_count = units[unit_index]["word_count"]
        best_score = abs(best_word_count - dynamic_target)

        for cursor in range(unit_index, len(units)):
            next_word_count = current_word_count + units[cursor]["word_count"]
            if cursor > unit_index and next_word_count > allowed_word_count:
                break

            current_word_count = next_word_count
            current_score = abs(current_word_count - dynamic_target)
            if current_score < best_score or (
                current_score == best_score and current_word_count > best_word_count
            ):
                best_index = cursor
                best_word_count = current_word_count
                best_score = current_score

        trimmed = _trim_span(text, units[unit_index]["start"], units[best_index]["end"])
        if trimmed[0] < trimmed[1]:
            spans.append(trimmed)
        unit_index = best_index + 1

    return spans


def _split_span_recursive(
    text: str,
    start: int,
    end: int,
    max_word_count: int,
    soft_overflow_ratio: float,
    same_sentence_penalty: int,
    sentence_boundary_penalty: int,
    fragment_boundary_penalty: int,
    short_underfill_ratio: float,
    short_underfill_penalty: int,
    pattern_index: int = 0,
) -> list[tuple[int, int]]:
    start, end = _trim_span(text, start, end)
    if start >= end:
        return []

    allowed_word_count = _allowed_word_count(max_word_count, soft_overflow_ratio)
    if _span_word_count(text, start, end) <= allowed_word_count:
        return [(start, end)]

    for idx in range(pattern_index, len(_DEFAULT_SPLIT_PATTERNS)):
        spans = _split_span_by_pattern(text, start, end, _DEFAULT_SPLIT_PATTERNS[idx])
        if len(spans) <= 1:
            continue

        groups: list[tuple[list[tuple[int, int]], bool]] = []
        for sub_start, sub_end in spans:
            if _span_word_count(text, sub_start, sub_end) <= allowed_word_count:
                groups.append(([(sub_start, sub_end)], True))
            else:
                groups.append(
                    (
                        _split_span_recursive(
                            text,
                            sub_start,
                            sub_end,
                            max_word_count,
                            soft_overflow_ratio,
                            same_sentence_penalty,
                            sentence_boundary_penalty,
                            fragment_boundary_penalty,
                            short_underfill_ratio,
                            short_underfill_penalty,
                            idx + 1,
                        ),
                        False,
                    )
                )

        refined = [span for group, _ in groups for span in group]
        if refined:
            if idx < _HARD_BOUNDARY_PATTERN_COUNT:
                return refined
            if idx == _SENTENCE_PATTERN_INDEX:
                return _pack_sentence_groups(
                    text,
                    groups,
                    max_word_count,
                    soft_overflow_ratio,
                    same_sentence_penalty,
                    sentence_boundary_penalty,
                    fragment_boundary_penalty,
                    short_underfill_ratio,
                    short_underfill_penalty,
                )
            return _pack_spans_balanced(
                text,
                refined,
                max_word_count,
                soft_overflow_ratio,
            )

    return _hard_split_span(text, start, end, max_word_count, soft_overflow_ratio)


def split_text_by_word_count(
    text: str,
    max_word_count: int = 32,
    soft_overflow_ratio: float = 0.12,
    same_sentence_penalty: int = 1,
    sentence_boundary_penalty: int = 4,
    fragment_boundary_penalty: int = 24,
    short_underfill_ratio: float = 0.5,
    short_underfill_penalty: int = 1,
) -> list[TextChunk]:
    """Split text on semantic boundaries while packing chunks near the word limit."""
    if max_word_count <= 0:
        raise ValueError("max_word_count must be greater than 0.")
    if soft_overflow_ratio < 0:
        raise ValueError("soft_overflow_ratio must be non-negative.")
    if min(
        same_sentence_penalty,
        sentence_boundary_penalty,
        fragment_boundary_penalty,
        short_underfill_penalty,
    ) < 0:
        raise ValueError("chunking penalties must be non-negative.")
    if short_underfill_ratio < 0:
        raise ValueError("short_underfill_ratio must be non-negative.")
    if not text.strip():
        return []

    spans = _split_span_recursive(
        text,
        0,
        len(text),
        max_word_count,
        soft_overflow_ratio,
        same_sentence_penalty,
        sentence_boundary_penalty,
        fragment_boundary_penalty,
        short_underfill_ratio,
        short_underfill_penalty,
    )

    result: list[TextChunk] = []
    for start, end in spans:
        chunk = _make_chunk(text, start, end)
        if chunk is not None:
            result.append(chunk)
    return result


def split_text(
    text: str,
    max_word_count: int = 32,
    soft_overflow_ratio: float = 0.12,
    same_sentence_penalty: int = 1,
    sentence_boundary_penalty: int = 4,
    fragment_boundary_penalty: int = 24,
    short_underfill_ratio: float = 0.5,
    short_underfill_penalty: int = 1,
) -> list[str]:
    return [
        chunk["text"]
        for chunk in split_text_by_word_count(
            text,
            max_word_count=max_word_count,
            soft_overflow_ratio=soft_overflow_ratio,
            same_sentence_penalty=same_sentence_penalty,
            sentence_boundary_penalty=sentence_boundary_penalty,
            fragment_boundary_penalty=fragment_boundary_penalty,
            short_underfill_ratio=short_underfill_ratio,
            short_underfill_penalty=short_underfill_penalty,
        )
    ]


def truncate_text_by_word_count(text: str, max_word_count: int) -> str:
    if max_word_count <= 0:
        return ""
    if word_count(text) <= max_word_count:
        return text.strip()
    chunks = split_text_by_word_count(text, max_word_count=max_word_count)
    return chunks[0]["text"] if chunks else ""


__all__ = [
    "TextChunk",
    "split_text",
    "split_text_by_word_count",
    "truncate_text_by_word_count",
    "word_count",
]
