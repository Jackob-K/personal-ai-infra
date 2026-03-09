from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from app.models import ProjectItem, ProjectSubtask


BASE_DIR = Path(__file__).resolve().parents[2]
PROJECTS_PATH = BASE_DIR / "data" / "runtime" / "projects.json"


def list_projects() -> list[ProjectItem]:
    if not PROJECTS_PATH.exists():
        return []
    with PROJECTS_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return [ProjectItem(**item) for item in raw]


def save_projects(projects: list[ProjectItem]) -> None:
    PROJECTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PROJECTS_PATH.open("w", encoding="utf-8") as f:
        json.dump([p.model_dump(mode="json") for p in projects], f, ensure_ascii=False, indent=2)


def create_project(name: str, role: str, deadline=None) -> ProjectItem:
    projects = list_projects()
    project = ProjectItem(
        id=str(uuid4()),
        name=name.strip(),
        role=role.strip().upper(),
        deadline=deadline,
        created_at=datetime.utcnow(),
    )
    projects.append(project)
    save_projects(projects)
    return project


def add_subtask(project_id: str, title: str, priority: int = 3) -> ProjectSubtask:
    projects = list_projects()
    project = next((p for p in projects if p.id == project_id), None)
    if project is None:
        raise ValueError(f"Project '{project_id}' not found")
    subtask = ProjectSubtask(id=str(uuid4()), title=title.strip(), priority=max(1, min(5, int(priority))))
    project.subtasks.append(subtask)
    save_projects(projects)
    return subtask


def update_project_meta(project_id: str, status: str | None = None, deadline: date | None = None) -> ProjectItem:
    projects = list_projects()
    project = next((p for p in projects if p.id == project_id), None)
    if project is None:
        raise ValueError(f"Project '{project_id}' not found")
    if status is not None:
        project.status = status
    project.deadline = deadline
    save_projects(projects)
    return project


def remove_subtask(project_id: str | None, subtask_id: str | None) -> bool:
    if not project_id or not subtask_id:
        return False
    projects = list_projects()
    project = next((p for p in projects if p.id == project_id), None)
    if project is None:
        return False
    before = len(project.subtasks)
    project.subtasks = [s for s in project.subtasks if s.id != subtask_id]
    if len(project.subtasks) == before:
        return False
    save_projects(projects)
    return True


def update_subtask(project_id: str, subtask_id: str, status: str, note: str | None = None) -> ProjectSubtask:
    projects = list_projects()
    project = next((p for p in projects if p.id == project_id), None)
    if project is None:
        raise ValueError(f"Project '{project_id}' not found")
    subtask = next((s for s in project.subtasks if s.id == subtask_id), None)
    if subtask is None:
        raise ValueError(f"Subtask '{subtask_id}' not found")

    allowed = {"todo", "in_progress", "submitted", "needs_revision", "done"}
    if status not in allowed:
        raise ValueError("Invalid subtask status")

    subtask.status = status
    if note:
        subtask.notes.append(note[:500])
    save_projects(projects)
    return subtask
