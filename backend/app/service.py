"""Service layer shared by the REST API and the MCP server.

Both interfaces are thin presentation layers over the same ledger logic; keeping
the projection in one place prevents the two from drifting (a risk called out in
the pivot notes). The HTTP path (`app.main`) and the MCP path (`app.mcp.server`)
both call `project_run_state`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app import crud, models


def project_run_state(
    db: Session,
    task: models.Task,
    values: dict[str, Any],
    waiting_for_human: bool,
) -> None:
    """Project a LangGraph Platform run's state into the Postgres ledger.

    The Platform thread is the source of truth; Postgres is a projection. This
    is **idempotent**: replaying the same `values` (e.g. a retried request) adds
    no duplicate draft versions, because each draft is keyed by its 1-based
    index and only inserted when that version is absent.
    """
    new_drafts = values.get("drafts") or []
    existing_versions = {d.version for d in crud.get_drafts(db, task.id)}
    for idx, content in enumerate(new_drafts, start=1):
        if idx not in existing_versions:
            crud.add_draft(db, task_id=task.id, content=content)

    reviewer_comments = values.get("reviewer_comments") or []
    feedback = reviewer_comments[-1] if reviewer_comments else None
    current_draft = new_drafts[-1] if new_drafts else None

    if waiting_for_human:
        new_status = models.TaskStatusEnum.WAITING_APPROVAL
    elif values.get("final_output"):
        new_status = models.TaskStatusEnum.COMPLETED
    else:
        new_status = task.status  # unchanged

    crud.update_task(
        db,
        task_id=task.id,
        status=new_status,
        current_draft=current_draft,
        feedback=feedback,
    )
