"""Compare a manual UTF-8 transcript with a JSON meeting export, fully locally."""
import argparse
import json
import re
import unicodedata
from pathlib import Path


def normalized(text):
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFC', text).casefold()))


def distance(reference, hypothesis):
    previous = list(range(len(hypothesis) + 1))
    for i, left in enumerate(reference, 1):
        current = [i]
        for j, right in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def score(reference, hypothesis):
    reference, hypothesis = normalized(reference), normalized(hypothesis)
    words, predicted = reference.split(), hypothesis.split()
    chars, predicted_chars = reference.replace(' ', ''), hypothesis.replace(' ', '')
    if not words:
        raise ValueError('Reference must contain words')
    word_errors, char_errors = distance(words, predicted), distance(chars, predicted_chars)
    return dict(reference_words=len(words), hypothesis_words=len(predicted), word_errors=word_errors,
                wer=round(word_errors / len(words), 4), character_errors=char_errors,
                cer=round(char_errors / len(chars), 4))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True, help='Manual transcript of the SAME audio, UTF-8 .txt')
    parser.add_argument('--hypothesis', type=Path, required=True, help='Meeting JSON export')
    args = parser.parse_args()
    meeting = json.loads(args.hypothesis.read_text(encoding='utf-8'))
    reference = args.reference.read_text(encoding='utf-8')
    result = {'primary': score(reference, ' '.join(s['text'] for s in meeting['segments']))}
    if meeting.get('asr_alternatives'):
        result['ru_pass'] = score(reference, ' '.join(s['text'] for s in meeting['asr_alternatives']))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
