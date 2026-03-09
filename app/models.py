from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class EmailClassifyRequest(BaseModel):
    subject: str = Field(default="", description="Email subject")
    body: str = Field(default="", description="Email body content")
    sender: str | None = None
    received_at: datetime | None = None


class ClassifyEmailResponse(BaseModel):
    role: str
    requires_action: bool
    suggested_duration_minutes: int
    priority: int = Field(ge=1, le=5)
    summary: str


class TimeBlock(BaseModel):
    start: datetime
    end: datetime
    label: str = ""


class PlanTaskRequest(BaseModel):
    role: str
    task_title: str
    duration_minutes: int = Field(gt=0, le=600)
    planning_date: date
    day_start: str | None = None
    day_end: str | None = None
    existing_events: list[TimeBlock] = Field(default_factory=list)


class PlanTaskResponse(BaseModel):
    role: str
    task_title: str
    planned_start: datetime | None
    planned_end: datetime | None
    status: str
    reason: str | None = None
    used_blocks: list[TimeBlock] = Field(default_factory=list)


class InboxAccountConfig(BaseModel):
    name: str
    host: str
    port: int = 993
    username: str
    password: str | None = None
    password_env: str | None = None
    folder: str = "INBOX"
    unseen_only: bool = True


class IngestImapRequest(BaseModel):
    accounts: list[InboxAccountConfig]
    max_per_account: int = Field(default=10, ge=1, le=100)


class RawEmailMessage(BaseModel):
    account_name: str
    message_id: str
    sender: str
    subject: str
    body: str
    received_at: datetime | None = None


class TaskProposal(BaseModel):
    id: str
    created_at: datetime
    status: Literal[
        "pending",
        "approved",
        "in_progress",
        "submitted",
        "needs_revision",
        "dispatched",
        "done",
        "rejected",
    ] = "pending"
    account_name: str
    message_id: str
    sender: str
    subject: str
    source_excerpt: str = ""
    role: str
    handling: Literal["review", "process", "needs_attention", "calendar"] = "review"
    summary: str
    requires_action: bool
    priority: int = Field(ge=1, le=5)
    duration_minutes: int = Field(gt=0, le=600)
    next_step: str
    bundle_key: str | None = None
    bundle_label: str | None = None
    project_id: str | None = None
    subtask_id: str | None = None
    task_group: str | None = None
    comments: list[str] = Field(default_factory=list)
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    calendar_event_uid: str | None = None


class IngestImapResponse(BaseModel):
    emails_count: int
    proposals_created: int
    proposals_updated: int = 0
    new_proposal_ids: list[str] = Field(default_factory=list)
    proposals: list[TaskProposal]


class ProposalListResponse(BaseModel):
    proposals: list[TaskProposal]


class ApproveProposalRequest(BaseModel):
    approve: bool = True
    planning_date: date | None = None
    duration_minutes: int | None = Field(default=None, gt=0, le=600)
    priority: int | None = Field(default=None, ge=1, le=5)
    role: str | None = None
    auto_schedule_to_caldav: bool = False


class ApproveProposalResponse(BaseModel):
    proposal: TaskProposal


class TravelEstimateRequest(BaseModel):
    origin: str
    destination: str
    departure_time: datetime | None = None
    mode: Literal["driving", "walking", "bicycling", "transit"] = "transit"


class TravelEstimateResponse(BaseModel):
    provider: str
    duration_minutes: int
    status: str
    detail: str | None = None


class ProjectSubtask(BaseModel):
    id: str
    title: str
    status: Literal["todo", "in_progress", "submitted", "needs_revision", "done"] = "todo"
    priority: int = Field(default=3, ge=1, le=5)
    notes: list[str] = Field(default_factory=list)


class ProjectItem(BaseModel):
    id: str
    name: str
    role: str
    status: Literal["open", "blocked", "waiting", "done"] = "open"
    deadline: date | None = None
    created_at: datetime
    notes: list[str] = Field(default_factory=list)
    subtasks: list[ProjectSubtask] = Field(default_factory=list)
