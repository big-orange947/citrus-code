from typing import Optional, Union
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...ai.types import Message


class SessionRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        populate_by_name=True,
    )


class SessionHeader(SessionRecord):
    type: Literal["session"] = "session"

    version: Literal[1] = 1

    id: str = Field(min_length=1)
    timestamp: int = Field(ge=0)
    cwd: str = Field(min_length=1)


class SessionEntryBase(SessionRecord):
    id: str = Field(min_length=1)

    parent_id: Optional[str] = Field(
        alias="parentId",
    )

    timestamp: int = Field(ge=0)


class SessionMessageEntry(SessionEntryBase):
    type: Literal["message"] = "message"

    message: Message
    task_start: bool = Field(default=False, alias="taskStart")


class CompactionEntry(SessionEntryBase):
    type: Literal["compaction"] = "compaction"
    summary: str = Field(min_length=1)
    retained_entry_ids: list[str] = Field(alias="retainedEntryIds", min_length=1)
    tokens_before: int = Field(alias="tokensBefore", ge=0)
    tokens_after: int = Field(alias="tokensAfter", ge=0)


class ContextMessageEntry(BaseModel):
    """A message in the active projection, including a synthetic summary."""

    model_config = ConfigDict(extra="forbid")
    id: str
    message: Message
    task_start: bool = False
    is_summary: bool = False


class ModelChangeEntry(SessionEntryBase):
    type: Literal["model_change"] = "model_change"

    provider: str = Field(min_length=1)

    model_id: str = Field(
        alias="modelId",
        min_length=1,
    )


class SessionCursorEntry(SessionRecord):
    """Persist the selected branch tip without creating a new tree node."""

    type: Literal["cursor"] = "cursor"
    entry_id: Optional[str] = Field(alias="entryId")
    timestamp: int = Field(ge=0)


SessionEntry = Annotated[
    Union[SessionMessageEntry, ModelChangeEntry, CompactionEntry],
    Field(discriminator="type"),
]


SessionLogEntry = Annotated[
    Union[SessionMessageEntry, ModelChangeEntry, CompactionEntry, SessionCursorEntry],
    Field(discriminator="type"),
]


SessionFileEntry = Annotated[
    Union[
        SessionHeader,
        SessionMessageEntry,
        ModelChangeEntry,
        CompactionEntry,
        SessionCursorEntry,
    ],
    Field(discriminator="type"),
]


class SessionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[Message]

    provider: Optional[str] = None
    model_id: Optional[str] = None
