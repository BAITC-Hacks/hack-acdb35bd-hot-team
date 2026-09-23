"""Model-independent word timing and speaker alignment helpers."""

import math
from collections import defaultdict

UNKNOWN = "SPEAKER_UNKNOWN"


def normalize_words(raw, start, end, text):
    """Only keep complete, ordered timing data; never silently lose text."""
    words = []
    for item in raw or []:
        try:
            left, right = float(item["start"]), float(item["end"])
            token = item["word"]
            if not isinstance(token, str) or not all(map(math.isfinite, (left, right))):
                return []
            left, right = max(start, left), min(end, right)
            if right < left or (words and left < words[-1]["start"]):
                return []
            words.append(dict(start=left, end=right, word=token))
        except (KeyError, TypeError, ValueError):
            return []
    if "".join(w["word"] for w in words).strip() != text.strip():
        return []
    return words


def speaker_for(start, end, turns):
    coverage = defaultdict(float)
    for left, right, speaker in turns:
        coverage[speaker] += max(0, min(end, right) - max(start, left))
    ranked = sorted(coverage.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] <= 0:
        return UNKNOWN, True
    if len(ranked) > 1 and abs(ranked[0][1] - ranked[1][1]) < 1e-6:
        return UNKNOWN, True
    speaker, duration = ranked[0]
    return speaker, duration < (end - start) * 0.6


def align_speakers(segments, turns):
    """Split on word boundaries, retaining a safe fallback for old/edited text."""
    aligned = []
    for segment in segments:
        words = normalize_words(segment.get("words"), segment["start"], segment["end"], segment["text"])
        if not words:
            speaker, uncertain = speaker_for(segment["start"], segment["end"], turns)
            aligned.append({**segment, "words": [], "speaker": speaker,
                            "uncertain": segment.get("uncertain", False) or uncertain})
            continue
        groups = []
        for word in words:
            speaker, uncertain = speaker_for(word["start"], word["end"], turns)
            if not groups or groups[-1]["speaker"] != speaker:
                groups.append(dict(start=word["start"], end=word["end"], text="",
                                   speaker=speaker, uncertain=segment.get("uncertain", False), words=[]))
            group = groups[-1]
            group["end"] = max(group["end"], word["end"])
            group["text"] += word["word"]
            group["words"].append(word)
            group["uncertain"] |= uncertain
        for group in groups:
            group["text"] = group["text"].strip()
            if group["text"]:
                aligned.append(group)
    for i, segment in enumerate(aligned):
        segment["id"] = i
    return aligned
