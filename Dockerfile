# Both base images have linux/amd64 and linux/arm64 manifests, pinned by digest.
FROM ollama/ollama:0.34.3@sha256:7ab595e4ead391f6818c7215297781282babe0701f6d9a9f8862ac591360a58b AS ollama
FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models/huggingface OLLAMA_MODELS=/models/ollama \
    DATA_DIR=/data ASR_BACKEND=cpu ASR_MODEL=Systran/faster-whisper-large-v3-turbo \
    LLM_BACKEND=ollama OLLAMA_URL=http://127.0.0.1:11434 OLLAMA_HOST=127.0.0.1:11434 \
    OLLAMA_MODEL=qwen3:4b OLLAMA_NO_CLOUD=1 OLLAMA_NUM_PARALLEL=1 \
    HF_HUB_DISABLE_TELEMETRY=1 PYANNOTE_METRICS_ENABLED=0 \
    OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.12.0
COPY --from=ollama /usr/bin/ollama /usr/bin/ollama
COPY --from=ollama /usr/lib/ollama /usr/lib/ollama
WORKDIR /app
ARG TARGETARCH
COPY docker/requirements-*.txt /app/docker/
RUN uv pip install --system --no-cache --require-hashes --torch-backend cpu -r docker/requirements-${TARGETARCH}.txt
COPY app /app/app
COPY scripts /app/scripts
COPY tests /app/tests
COPY docker/entrypoint.py /app/docker/entrypoint.py
COPY pyproject.toml /app/pyproject.toml
RUN mkdir -p /data /models
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
ENTRYPOINT ["python", "docker/entrypoint.py"]
CMD ["serve"]
