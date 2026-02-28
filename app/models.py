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
    status: Literal["pending", "approved", "rejected"] = "pending"
    account_name: str
    message_id: str
    sender: str
    subject: str
    role: str
    summary: str
    requires_action: bool
    priority: int = Field(ge=1, le=5)
    duration_minutes: int = Field(gt=0, le=600)
    next_step: str
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    calendar_event_uid: str | None = None


class IngestImapResponse(BaseModel):
    emails_count: int
    proposals_created: int
    proposals: list[TaskProposal]


class ProposalListResponse(BaseModel):
    proposals: list[TaskProposal]


class ApproveProposalRequest(BaseModel):
    approve: bool = True
    planning_date: date | None = None
    duration_minutes: int | None = Field(default=None, gt=0, le=600)
    priority: int | None = Field(default=None, ge=1, le=5)
    role: str | None = None
    auto_schedule_to_caldav: bool = True


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
