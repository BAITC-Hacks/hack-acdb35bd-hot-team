"""Installation step only: downloads weights, never reads meeting data."""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import ASR_MODEL, LLM_MODEL, DIAR_MODEL

os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
from huggingface_hub import snapshot_download

parser = argparse.ArgumentParser()
parser.add_argument("models", nargs="+", choices=["asr", "llm", "diarization"])
args = parser.parse_args()
for name in args.models:
    repo = {"asr": ASR_MODEL, "llm": LLM_MODEL, "diarization": DIAR_MODEL}[name]
    print(f"Preparing {name}: {repo}", flush=True)
    try:
        if name == "diarization":
            from pyannote.audio import Pipeline

            pipeline = Pipeline.from_pretrained(
                repo, token=os.environ.get("HF_TOKEN") or None
            )
            del pipeline
        else:
            snapshot_download(repo)
    except Exception as exc:
        print(
            f"Download failed ({type(exc).__name__}). Check your connection and model access. For pyannote accept the model conditions and set HF_TOKEN in .env.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"{name}: ready", flush=True)
