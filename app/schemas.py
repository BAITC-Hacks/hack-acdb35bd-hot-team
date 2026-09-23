from datetime import date
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


class Task(BaseModel):
    title: str = Field(min_length=1, max_length=1000)
    owner: str | None = Field(default=None, max_length=200)
    deadline_text: str | None = Field(default=None, max_length=300)
    due_date: date | None = None
    source_ids: list[int] = Field(default_factory=list, max_length=30)
    evidence: str = Field(default="", max_length=2000)
    status: Literal["open", "done"] = "open"
    needs_review: bool = True


class Protocol(BaseModel):
    summary: str = Field(default="", max_length=20000)
    decisions: list[str] = Field(default_factory=list, max_length=100)
    tasks: list[Task] = Field(default_factory=list, max_length=100)
    approved: bool = False


class ProcessRequest(BaseModel):
    stage: Literal["transcribe", "diarize", "analyze"]
    language: Literal["kk", "ru", "auto"] = "kk"
    num_speakers: int | None = Field(default=None, ge=2, le=20)
