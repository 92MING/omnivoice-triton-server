from __future__ import annotations

from chunking import split_text_by_word_count, word_count


def test_word_count_mixed_language_numbers_and_emoji() -> None:
    assert word_count("Hello 世界 123.45 🚀 test") == 6


def test_split_packs_near_limit_without_breaking_sentences() -> None:
    text = (
        "First sentence stays with its neighbor when there is room. "
        "Second sentence should be packed together. "
        "第三句包含中文内容，也应该按照字符计数。"
        "最后一句用于确认不会粗暴按固定字符数切开。"
    )
    chunks = split_text_by_word_count(text, max_word_count=24, soft_overflow_ratio=0.12)

    assert len(chunks) > 1
    assert "".join(chunk["text"] for chunk in chunks).replace(" ", "") == text.replace(" ", "")
    assert all(chunk["word_count"] <= 27 for chunk in chunks)
    assert all(chunk["text"] for chunk in chunks)


def test_hard_split_long_mixed_span() -> None:
    text = "这是一个没有明显标点的长中文片段withEnglishWordsAnd123Numbers混在一起用于测试硬切分逻辑" * 3
    chunks = split_text_by_word_count(text, max_word_count=18, soft_overflow_ratio=0.12)

    assert len(chunks) > 1
    assert all(chunk["word_count"] <= 21 for chunk in chunks)
    assert "".join(chunk["text"] for chunk in chunks) == text


def test_complete_sentence_does_not_merge_into_split_sentence_fragment() -> None:
    text = (
        "今天我们要验证新的 chunking 策略，它不能只是粗暴地按照字符数切开。 "
        "The system should preserve semantic boundaries, pack nearby clauses together, "
        "and still respect the expected limit. "
        "例如 2026.05 的版本里，用户可能输入中文、English、12345、emoji 🚀😊，"
        "甚至没有空格的长句子，所以切分必须稳定。"
    )
    chunks = split_text_by_word_count(text, max_word_count=32, soft_overflow_ratio=0.12)
    texts = [chunk["text"] for chunk in chunks]

    assert texts[1].endswith("expected limit.")
    assert not texts[1].startswith("例如")
    assert "例如 2026.05" in texts[2]


def test_multiple_complete_sentences_are_still_packed() -> None:
    text = " ".join(f"Sentence {i} stays complete." for i in range(1, 13))
    chunks = split_text_by_word_count(text, max_word_count=32, soft_overflow_ratio=0.12)

    assert [chunk["word_count"] for chunk in chunks] == [24, 24]
    assert chunks[0]["text"].endswith("Sentence 6 stays complete.")
    assert chunks[1]["text"].startswith("Sentence 7 stays complete.")
