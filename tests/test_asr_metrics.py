from scripts.evaluate_asr import score
import pytest


def test_mixed_language_metrics_keep_kazakh_letters():
    assert score('Жақсы, отчёт дайын!', 'жақсы отчёт дайын')['wer'] == 0
    result = score('Әлі есеп дайын', 'Али есеп')
    assert result['word_errors'] == 2
    assert result['wer'] == .6667 and result['cer'] > 0
    assert score('есеп', 'есеп готов сейчас')['wer'] == 2
    with pytest.raises(ValueError):
        score('', 'noise')
