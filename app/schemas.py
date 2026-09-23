from datetime import date
from uuid import uuid4
from typing import Literal
from pydantic import BaseModel, Field, model_validator


class Segment(BaseModel):
    id: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(max_length=10000)
    speaker: str = Field(default="SPEAKER_UNKNOWN", max_length=100)
    uncertain: bool = False

    @model_validator(mode="after")
    def interval(self):
        if self.end < self.start:
            raise ValueError("Конец реплики раньше начала")
        return self


class TranscriptEdit(BaseModel):
    segments: list[Segment] = Field(max_length=10000)
    speakers: dict[str, str]

    @model_validator(mode="after")
    def unique_ids(self):
        if len({s.id for s in self.segments}) != len(self.segments):
            raise ValueError("Идентификаторы реплик должны быть уникальны")
        if len(self.speakers) > 100 or any(
            len(k) > 100 or len(v) > 200 for k, v in self.speakers.items()
        ):
            raise ValueError("Слишком длинные имена участников")
        return self


class Quote(BaseModel):
    source_id: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=2000)


class Task(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[a-f0-9]{32}$")
    title: str = Field(min_length=1, max_length=1000)
    topic_id: str | None = Field(default=None, max_length=80)
    owner: str | None = Field(default=None, max_length=200)
    deadline_text: str | None = Field(default=None, max_length=300)
    due_date: date | None = None
    source_ids: list[int] = Field(default_factory=list, max_length=30)
    evidence: str = Field(default="", max_length=2000)
    context_evidence: list[Quote] = Field(default_factory=list, max_length=8)
    status: Literal["open", "in_progress", "done"] = "open"
    priority: Literal["unspecified", "normal", "high"] = "unspecified"
    direction: Literal["other", "finance", "procurement", "legal", "safety", "operations", "it", "hr"] = "other"
    classification_reason: str = Field(default="", max_length=1000)
    needs_review: bool = True


class Topic(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=20000)
    source_ids: list[int] = Field(default_factory=list, max_length=10000)


class Protocol(BaseModel):
    topics: list[Topic] = Field(default_factory=list, max_length=30)
    summary: str = Field(default="", max_length=20000)
    decisions: list[str] = Field(default_factory=list, max_length=100)
    tasks: list[Task] = Field(default_factory=list, max_length=100)
    approved: bool = False

    @model_validator(mode="after")
    def unique_task_ids(self):
        if len({t.id for t in self.tasks}) != len(self.tasks):
            raise ValueError("Идентификаторы поручений должны быть уникальны")
        ids = [t.id for t in self.topics]
        if len(set(ids)) != len(ids):
            raise ValueError("Идентификаторы тем должны быть уникальны")
        if any(t.topic_id and t.topic_id not in ids for t in self.tasks):
            raise ValueError("Поручение ссылается на отсутствующую тему")
        return self


class TaskMove(BaseModel):
    status: Literal["open", "in_progress", "done"]
    expected_status: Literal["open", "in_progress", "done"]


class ProcessRequest(BaseModel):
    stage: Literal["prepare", "transcribe", "diarize", "analyze"]
    language: Literal["kk", "ru", "auto", "ru_kk"] = "kk"
    num_speakers: int | None = Field(default=None, ge=2, le=20)


class VoiceEnrollment(BaseModel):
    speaker: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    consent: bool = False
