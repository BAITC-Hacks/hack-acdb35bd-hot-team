from app.speech import align_speakers, normalize_words, speaker_for


def test_mixed_languages_split_without_losing_words():
    words = [dict(start=0, end=1, word="Жақсы,"),
             dict(start=1, end=2, word=" отчёт"),
             dict(start=2, end=3, word=" завтра.")]
    segments = [dict(id=7, start=0, end=3, text="Жақсы, отчёт завтра.", words=words, uncertain=False)]
    result = align_speakers(segments, [(0, 1, "A"), (1, 3, "B")])
    assert [s["speaker"] for s in result] == ["A", "B"]
    assert [s["text"] for s in result] == ["Жақсы,", "отчёт завтра."]
    assert [s["id"] for s in result] == [0, 1]
    assert all(not s["uncertain"] for s in result)
    assert align_speakers(result, [(0, 1, "A"), (1, 3, "B")]) == result


def test_coverage_accumulates_across_turns_and_ties_are_unknown():
    assert speaker_for(0, 10, [(0, 3, "A"), (3, 7, "B"), (7, 10, "A")]) == ("A", False)
    assert speaker_for(0, 2, [(0, 1, "A"), (1, 2, "B")]) == ("SPEAKER_UNKNOWN", True)
    assert speaker_for(0, 2, []) == ("SPEAKER_UNKNOWN", True)
    assert speaker_for(0, 0, [(0, 2, "A")]) == ("SPEAKER_UNKNOWN", True)


def test_edited_or_incomplete_words_never_replace_transcript():
    segment = dict(id=0, start=0, end=2, text="Исправленный текст", uncertain=True,
                   words=[dict(start=0, end=1, word="Старый текст")])
    result = align_speakers([segment], [(0, 2, "A")])
    assert result[0]["text"] == "Исправленный текст"
    assert result[0]["words"] == [] and result[0]["uncertain"]
    assert normalize_words([dict(start=float("nan"), end=1, word="a")], 0, 2, "a") == []


def test_missing_word_coverage_is_flagged():
    segment = dict(id=0, start=0, end=1, text="Да", words=[dict(start=0, end=1, word="Да")])
    result = align_speakers([segment], [(0, .4, "A")])
    assert result[0]["speaker"] == "A" and result[0]["uncertain"]


def test_alternative_uses_word_time_not_segment_index():
    from app.speech import attach_alternatives
    segments = [dict(start=0, end=1, text="Жақсы"), dict(start=1, end=2, text="Отчёт.")]
    alternative = [dict(start=0, end=2, text="Хорошо отчёт", words=[
        dict(start=0, end=1, word="Хорошо"), dict(start=1, end=2, word=" отчёт")])]
    attach_alternatives(segments, alternative)
    assert segments[0]['alternative_text'] == 'Хорошо' and segments[0]['uncertain']
    assert 'alternative_text' not in segments[1]


def test_repetition_guard_catches_word_and_syllable_loops():
    from app.speech import repetitive_text
    assert repetitive_text('қалайсыздар ' * 6)
    assert repetitive_text('Әріңіздіңіздіңіздіңіздіңіздіңіздіңіздіңіздіңіздіңіздіңіздіңіздіңіз')
    assert not repetitive_text('Жақсы, отчётты жұма күні жіберемін.')
