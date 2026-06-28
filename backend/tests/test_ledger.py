"""Invariants of the governance ledger (Draft history + Approval record).

These are the properties that make AxonRelay a *ledger* rather than a cache:
draft versions are monotonic, approvals are append-only, and the approval inbox
only surfaces tasks that genuinely await the operator's decision.
"""

from app import crud, models


def _task(db, **kw):
    return crud.create_task(db, thread_id=kw.pop("thread_id", "thread-1"), title=kw.pop("title", "t"), **kw)


def test_add_draft_assigns_monotonic_versions(db):
    task = _task(db)

    crud.add_draft(db, task_id=task.id, content="first")
    crud.add_draft(db, task_id=task.id, content="second")
    crud.add_draft(db, task_id=task.id, content="third")

    drafts = crud.get_drafts(db, task.id)
    assert [d.version for d in drafts] == [1, 2, 3]
    assert [d.content for d in drafts] == ["first", "second", "third"]


def test_record_approval_is_append_only_and_ordered(db, self_actor):
    task = _task(db)

    crud.record_approval(db, task_id=task.id, reviewer_actor_id=self_actor.id, action="rejected", comment="redo")
    crud.record_approval(db, task_id=task.id, reviewer_actor_id=self_actor.id, action="approved", comment="ok")

    approvals = crud.get_approvals(db, task.id)
    assert [a.action for a in approvals] == ["rejected", "approved"]
    assert approvals[0].reviewer_actor_id == self_actor.id


def test_pending_approvals_only_returns_waiting_tasks_for_the_approver(db, self_actor):
    approver_role = models.AssignmentRoleEnum.APPROVER

    # Task the operator must approve -> appears.
    waiting = _task(db, thread_id="t-waiting")
    crud.update_task(db, task_id=waiting.id, status=models.TaskStatusEnum.WAITING_APPROVAL)
    crud.create_task_assignment(db, task_id=waiting.id, actor_id=self_actor.id, role=approver_role)

    # Same status but operator is only a reviewer -> excluded.
    reviewing = _task(db, thread_id="t-reviewing")
    crud.update_task(db, task_id=reviewing.id, status=models.TaskStatusEnum.WAITING_APPROVAL)
    crud.create_task_assignment(
        db, task_id=reviewing.id, actor_id=self_actor.id, role=models.AssignmentRoleEnum.REVIEWER
    )

    # Operator is approver but task is not waiting -> excluded.
    draft = _task(db, thread_id="t-draft")
    crud.create_task_assignment(db, task_id=draft.id, actor_id=self_actor.id, role=approver_role)

    pending = crud.list_pending_approvals(db, actor_id=self_actor.id)
    assert [t.thread_id for t in pending] == ["t-waiting"]


def test_pending_approvals_does_not_duplicate_on_repeated_assignment(db, self_actor):
    """A task must surface once in the inbox even with a duplicate approver row."""
    approver_role = models.AssignmentRoleEnum.APPROVER
    task = _task(db, thread_id="t-dup")
    crud.update_task(db, task_id=task.id, status=models.TaskStatusEnum.WAITING_APPROVAL)
    crud.create_task_assignment(db, task_id=task.id, actor_id=self_actor.id, role=approver_role)
    crud.create_task_assignment(db, task_id=task.id, actor_id=self_actor.id, role=approver_role)

    pending = crud.list_pending_approvals(db, actor_id=self_actor.id)
    assert [t.thread_id for t in pending] == ["t-dup"]


def test_create_assignment_is_idempotent_on_task_actor_role(db, self_actor):
    """A repeat (task, actor, role) assignment returns the existing row, not a dup."""
    task = _task(db, thread_id="t-idem")
    role = models.AssignmentRoleEnum.APPROVER

    first = crud.create_task_assignment(db, task_id=task.id, actor_id=self_actor.id, role=role)
    second = crud.create_task_assignment(db, task_id=task.id, actor_id=self_actor.id, role=role)

    assert first.id == second.id
    assert len(crud.get_task_assignments(db, task.id)) == 1

    # A different role for the same actor is a distinct assignment.
    crud.create_task_assignment(db, task_id=task.id, actor_id=self_actor.id, role=models.AssignmentRoleEnum.REVIEWER)
    assert len(crud.get_task_assignments(db, task.id)) == 2
