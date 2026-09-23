"""Persist lightweight progress without publishing a worker's partial output."""
import time
from datetime import datetime, timezone
from . import store


class Progress:
    def __init__(self, meeting, stage, combined=False):
        self.meeting = meeting
        self.stage = "prepare" if combined else stage
        self.offset = 70 if combined and stage == "diarize" else 0
        self.scale = (0.3 if stage == "diarize" else 0.7) if combined else 1
        self.combined = combined
        self.step = stage
        self.started_at = datetime.now(timezone.utc).isoformat()
        if combined and stage == "diarize":
            self.started_at = (meeting.get("progress") or {}).get("started_at", self.started_at)
        self.last_write = 0
        self.percent = 0

    def update(self, percent, label, estimated=False, force=False):
        percent = self.offset + self.scale * max(0, min(100, percent))
        percent = max(self.percent, min(99, int(percent)))
        estimated = estimated or self.combined
        if self.combined:
            label = ("Распознавание · " if self.step == "transcribe" else "Разделение голосов · ") + label
        now = time.monotonic()
        if not force and now - self.last_write < 0.5:
            return
        self.percent = percent
        self.last_write = now
        value = dict(stage=self.stage, percent=percent, label=label,
                     estimated=estimated, started_at=self.started_at)
        self.meeting['progress'] = value
        current = store.get(self.meeting['id'])
        if current:
            current['progress'] = value
            store.save(current)

    def complete(self):
        self.meeting['progress'] = dict(stage=self.stage, percent=100, label='Готово',
            estimated=False, started_at=self.started_at)
