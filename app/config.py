import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
configured_data = Path(os.environ.get("DATA_DIR", "data"))
DATA = (configured_data if configured_data.is_absolute() else ROOT / configured_data).resolve()
DATA.mkdir(parents=True, exist_ok=True)
ASR_MODEL = os.environ.get("ASR_MODEL", "mlx-community/whisper-large-v3-turbo")
ASR_BACKEND = os.environ.get("ASR_BACKEND", "mlx")
LLM_MODEL = os.environ.get("LLM_MODEL", "mlx-community/Qwen3-4B-4bit")
DIAR_MODEL = os.environ.get("DIAR_MODEL", "pyannote/speaker-diarization-community-1")
MAX_UPLOAD = 250 * 1024 * 1024
LLM_BACKEND = os.environ.get("LLM_BACKEND", "mlx")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:4b")
