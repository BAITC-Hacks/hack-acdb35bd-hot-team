# Локальное сравнение Sybyrla и Whisper Turbo

Подготовка и сравнение профиля Sybyrla на Apple Silicon. Этот профиль используется в рабочей демонстрации; чистая установка по `.env_example` остаётся на Whisper Turbo. Аудио и результаты сохраняются только в gitignored `data/`; веса — в `models/` и кэше Hugging Face. Во время inference используется сетевой запрет из `app.worker`: внешние соединения запрещены.

## Подготовка

Используйте установленный Mac-профиль проекта. Исходный checkpoint занимает около 6,2 ГБ, MLX FP16 — около 3,1 ГБ. Нужен дополнительный запас для памяти/swap и временных файлов. Ничего из реальных записей и результатов не добавляйте в Git.

Скачайте только веса и конфигурацию фиксированной версии (загрузка модели требует интернета, аудио не отправляется):

```sh
uv run --no-sync python - <<'PY'
from huggingface_hub import snapshot_download
print(snapshot_download('InflexionLab/sybyrla',
    revision='c895cb3067c160d30e83e77f608dd47504a119b8',
    allow_patterns=['config.json', 'model*.safetensors', 'model.safetensors.index.json'],
    max_workers=2))
PY
```

Подставьте напечатанный абсолютный путь:

```sh
uv run --no-sync python scripts/convert_whisper_hf_mlx.py /absolute/path/to/snapshot models/sybyrla-mlx-fp16
```

Конвертер работает только с safetensors, не запускает код из репозитория модели и проверяет все имена и размеры параметров. Преобразование FP32 → FP16 не является дообучением. Выходная папка не должна существовать. Используются стандартные головы выравнивания MLX; точность таймкодов дообученной модели отдельно не измерена.

## Одинаковый вход и параметры

```sh
mkdir -p data/evaluation/sybyrla
ffmpeg -i /absolute/path/to/recording.mp3 -t 60 -vn -ac 1 -ar 16000 data/evaluation/sybyrla/clip-60s.wav
uv run --no-sync python scripts/benchmark_mlx_asr.py --audio data/evaluation/sybyrla/clip-60s.wav --model mlx-community/whisper-large-v3-turbo --output data/evaluation/sybyrla/turbo-kk.json
uv run --no-sync python scripts/benchmark_mlx_asr.py --audio data/evaluation/sybyrla/clip-60s.wav --model models/sybyrla-mlx-fp16 --output data/evaluation/sybyrla/sybyrla-kk.json
```

Запускайте модели последовательно. Язык по умолчанию `kk`, задача `transcribe`, температура 0, контекст предыдущего сегмента отключён, таймкоды слов включены. JSON содержит параметры, SHA-256 входа, разрешённый локальный путь модели и время (включая загрузку модели, без скачивания и конвертации). Один запуск не является стабильным бенчмарком скорости.

Для WER/CER нужен дословный ручной эталон именно этого фрагмента:

```sh
uv run --no-sync python scripts/evaluate_asr.py --reference data/evaluation/sybyrla/reference.txt --hypothesis data/evaluation/sybyrla/sybyrla-kk.json
```

Метрика из карточки автора (~17,7% WER на KSC2) не является оценкой наших записей. Эталонные протоколы совещаний также нельзя считать дословной разметкой телефонного фрагмента.

Источники: [карточка Sybyrla](https://huggingface.co/InflexionLab/sybyrla), [конвертер Apple MLX](https://github.com/ml-explore/mlx-examples/blob/main/whisper/convert.py).

## Результат прогона 2026-09-23

Первые 60 секунд предоставленной телефонной записи, один и тот же mono PCM 16 кГц WAV; язык `kk` для обеих моделей. Turbo: 26,00 с, Sybyrla MLX FP16: 96,71 с (около 3,7 раза медленнее). Обе модели завершили локальный запуск с таймкодами слов.

У Turbo обнаружены длинные текстовые повторы, у Sybyrla получился более связный диалог. У Sybyrla сохранились повторяющиеся числовые последовательности и возможные ошибки в именах/словах. Это качественное наблюдение по выходным текстам, не подтверждение точности по аудио. WER/CER и качество выделения поручений не измерены; это не доказательство надёжности на всех смешанных совещаниях. После этого сравнения рабочая демонстрация на Mac переключена на Sybyrla. Для новой установки нужен явный выбор `ASR_MODEL=models/sybyrla-mlx-fp16` после преобразования весов; Docker остаётся на Turbo.

Результаты и сравнительный отчёт находятся локально в `data/evaluation/sybyrla/`, без публикации. Скачанный исходный checkpoint после конвертации удалён из HF-кэша для освобождения 6,17 ГБ; локальная MLX-копия (~3,09 ГБ) сохранена в `models/sybyrla-mlx-fp16/`.
