"""One local MLX ASR run. Input/output recordings and text must stay gitignored."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Reuse the application's offline-only network guard and cached-model lookup.
from app.worker import cached
import mlx_whisper


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audio', type=Path, required=True)
    p.add_argument('--model', required=True)
    p.add_argument('--language', choices=['kk', 'ru'], default='kk')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    model = cached(args.model)
    start = time.monotonic()
    result = mlx_whisper.transcribe(str(args.audio), path_or_hf_repo=model,
        language=args.language, task='transcribe', condition_on_previous_text=False,
        word_timestamps=True, temperature=0.0, verbose=False)
    result['benchmark'] = dict(model=args.model, resolved_model=model,
        language=args.language, elapsed_seconds=round(time.monotonic()-start, 2),
        audio_sha256=hashlib.sha256(args.audio.read_bytes()).hexdigest(),
        condition_on_previous_text=False, temperature=0, word_timestamps=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result['benchmark'], ensure_ascii=False))


if __name__ == '__main__':
    main()
