"""Service layer shared by the REST API and the MCP server.

Both interfaces are thin presentation layers over the same ledger logic; keeping
the projection in one place prevents the two from drifting (a risk called out in
the pivot notes). The HTTP path (`app.main`) and the MCP path (`app.mcp.server`)
both call `project_run_state`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app import crud, langgraph_client, models


def project_run_state(
    db: Session,
    task: models.Task,
    values: dict[str, Any],
    waiting_for_human: bool,
) -> None:
    """Project a LangGraph Platform run's state into the Postgres ledger.

    The Platform thread is the source of truth; Postgres is a projection. This
    is **idempotent**: replaying the same `values` (e.g. a retried request) adds
    no duplicate draft versions, because a draft is only inserted when neither
    its position nor its content is already in the ledger.
    """
    new_drafts = values.get("drafts") or []
    existing = crud.get_drafts(db, task.id)
    for idx, content in enumerate(new_drafts, start=1):
        # Match by position *and* content. Positions alone are not enough:
        # record_approval writes a reviewer's modified draft as a new version
        # before the graph resumes, so if that resume fails the ledger is one
        # version ahead of the graph and the graph's next draft would land on
        # an index that already exists here. Same content at the same index
        # (a replay, or the graph echoing the modified draft) adds nothing;
        # different content is a draft the ledger has not seen and is
        # appended as the next version.
        if idx <= len(existing):
            if existing[idx - 1].content == content or any(d.content == content for d in existing[idx - 1 :]):
                continue
        elif existing and existing[-1].content == content:
            continue
        existing.append(crud.add_draft(db, task_id=task.id, content=content))

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


async def refresh_from_platform(db: Session, task: models.Task) -> bool:
    """Read the thread's current state from Platform and project it. Returns "waiting".

    This is how a task whose decision was recorded but never confirmed gets
    back to WAITING_APPROVAL: not by assuming anything about the failed call,
    but because Platform itself says the graph is waiting for a human. If the
    graph already answered and moved on, the projection shows that instead.
    """
    state = await langgraph_client.get_state(task.thread_id)
    waiting = langgraph_client.is_waiting_for_human(state)
    project_run_state(db, task, langgraph_client.extract_values(state), waiting)
    return waiting


async def settle_failed_delivery(db: Session, task: models.Task, approval_id: int, exc: BaseException) -> None:
    """After a decision's one delivery attempt failed, put the task where Platform says it is.

    * Platform not configured: nothing was sent, so the decision is reopened.
    * Anything else may have been accepted (a timeout after acceptance looks
      like any other error), so nothing is assumed: the thread's state is read
      and projected. Only if Platform reports the graph waiting does the task
      return to WAITING_APPROVAL.
    * If the state cannot be read, or the request was cancelled, the task stays
      APPROVED / REJECTED - closed to new decisions - until `refresh_task`
      reads it. Reopening blind would let a second decision answer a question
      the graph may already have moved past.
    """
    if isinstance(exc, langgraph_client.PlatformNotConfiguredError):
        crud.reopen_decision(db, task.id, approval_id)
        return
    if not isinstance(exc, Exception):
        return
    db.rollback()
    try:
        await refresh_from_platform(db, task)
    except Exception:  # noqa: BLE001 - unreadable state leaves the task closed, by design
        db.rollback()
