"""Projection of LangGraph Platform thread state into the Postgres ledger.

The Platform thread is the source of truth; Postgres is a projection rebuilt
from run results. `project_run_state` is the shared function that **both** the
HTTP path (`app.main`) and the MCP path (`app.mcp.server`) call, so testing it
covers both interfaces. It must be idempotent — replaying the same run result
(e.g. a retried request) must not duplicate draft versions or corrupt status.
"""

from app import crud, models
from app.service import project_run_state


def _task(db):
    return crud.create_task(db, thread_id="thread-1", title="Compare X vs Y")


def test_projection_does_not_duplicate_drafts_on_replay(db):
    task = _task(db)
    values = {"drafts": ["draft v1"], "reviewer_comments": ["needs more detail"]}

    project_run_state(db, task, values, waiting_for_human=True)
    project_run_state(db, task, values, waiting_for_human=True)  # replay

    drafts = crud.get_drafts(db, task.id)
    assert [d.version for d in drafts] == [1]
    assert task.feedback == "needs more detail"
    assert task.current_draft == "draft v1"


def test_projection_marks_waiting_for_approval(db):
    task = _task(db)
    project_run_state(db, task, {"drafts": ["d1"], "reviewer_comments": ["c1"]}, waiting_for_human=True)
    assert task.status == models.TaskStatusEnum.WAITING_APPROVAL


def test_projection_marks_completed_on_final_output(db):
    task = _task(db)
    values = {"drafts": ["d1", "final"], "reviewer_comments": ["c1"], "final_output": "final"}

    project_run_state(db, task, values, waiting_for_human=False)

    assert task.status == models.TaskStatusEnum.COMPLETED
    assert [d.version for d in crud.get_drafts(db, task.id)] == [1, 2]


def test_projection_appends_new_draft_across_revision_rounds(db):
    task = _task(db)
    project_run_state(db, task, {"drafts": ["d1"], "reviewer_comments": ["c1"]}, waiting_for_human=True)
    # Rejected -> writer produced a second draft; replay-safe append of v2 only.
    project_run_state(db, task, {"drafts": ["d1", "d2"], "reviewer_comments": ["c1", "c2"]}, waiting_for_human=True)

    drafts = crud.get_drafts(db, task.id)
    assert [d.version for d in drafts] == [1, 2]
    assert drafts[1].content == "d2"
