"""Persist lightweight progress without publishing a worker's partial output."""
import time
from datetime import datetime, timezone
from . import store


class Progress:
    def __init__(self, meeting, stage):
        self.meeting = meeting
        self.stage = stage
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.last_write = 0
        self.percent = 0

    def update(self, percent, label, estimated=False, force=False):
        percent = max(self.percent, min(99, max(0, int(percent))))
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
