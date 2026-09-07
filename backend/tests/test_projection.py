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


def test_projection_keeps_a_reviewers_version_the_graph_never_saw(db, self_actor):
    """record_approval writes a modified draft *before* the graph resumes.

    If that resume fails, the ledger is one version ahead of the graph. When
    the graph later produces its own next draft at the same index, it must
    be appended as a further version, not silently dropped because the index
    is taken - the operator would otherwise be shown a draft with no row.
    """
    task = _task(db)
    project_run_state(db, task, {"drafts": ["d1"], "reviewer_comments": ["c1"]}, waiting_for_human=True)
    crud.record_approval(db, task.id, self_actor.id, "approved", modified_draft="operator edit")  # v2
    # The resume "failed"; the graph revises on its own and comes back with a second draft.
    project_run_state(db, task, {"drafts": ["d1", "d2-from-graph"], "reviewer_comments": ["c1", "c2"]}, True)

    drafts = crud.get_drafts(db, task.id)
    assert [d.content for d in drafts] == ["d1", "operator edit", "d2-from-graph"]
    assert task.current_draft == "d2-from-graph"


def test_projection_does_not_duplicate_the_modified_draft_the_graph_echoes(db, self_actor):
    """Happy path: the graph appends the modified draft at the index the ledger already used."""
    task = _task(db)
    project_run_state(db, task, {"drafts": ["d1"], "reviewer_comments": ["c1"]}, waiting_for_human=True)
    crud.record_approval(db, task.id, self_actor.id, "approved", modified_draft="operator edit")  # v2
    project_run_state(db, task, {"drafts": ["d1", "operator edit"], "final_output": "operator edit"}, False)

    assert [d.content for d in crud.get_drafts(db, task.id)] == ["d1", "operator edit"]
