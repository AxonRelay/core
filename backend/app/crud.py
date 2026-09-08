"""CRUD operations for database models (personal PoC, post-migration 003)."""

from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import ledger, models

# ========== Actor Operations ==========


def get_actor(db: Session, actor_id: int):
    return db.query(models.Actor).filter(models.Actor.id == actor_id).first()


def get_actors(db: Session, actor_type: models.ActorTypeEnum | None = None, skip: int = 0, limit: int = 100):
    query = db.query(models.Actor)
    if actor_type:
        query = query.filter(models.Actor.type == actor_type)
    return query.offset(skip).limit(limit).all()


def get_self_actor(db: Session):
    """Get the single human actor representing the operator."""
    return db.query(models.Actor).filter(models.Actor.type == models.ActorTypeEnum.HUMAN).first()


# ========== AgentDefinition Operations ==========


def get_agent_definition(db: Session, agent_id: int):
    return db.query(models.AgentDefinition).filter(models.AgentDefinition.id == agent_id).first()


def get_agent_definition_by_actor(db: Session, actor_id: int):
    return db.query(models.AgentDefinition).filter(models.AgentDefinition.actor_id == actor_id).first()


def get_agent_definitions(
    db: Session,
    agent_type: models.AgentTypeEnum | None = None,
    is_active: bool | None = None,
    skip: int = 0,
    limit: int = 100,
):
    query = db.query(models.AgentDefinition)
    if agent_type:
        query = query.filter(models.AgentDefinition.agent_type == agent_type)
    if is_active is not None:
        query = query.filter(models.AgentDefinition.is_active == is_active)
    return query.offset(skip).limit(limit).all()


def create_agent_definition(
    db: Session,
    name: str,
    agent_type: models.AgentTypeEnum,
    description: str | None = None,
    config: dict | None = None,
):
    db_actor = models.Actor(type=models.ActorTypeEnum.AI, name=name)
    db.add(db_actor)
    db.flush()

    db_agent = models.AgentDefinition(
        actor_id=db_actor.id, agent_type=agent_type, description=description, config=config
    )
    db.add(db_agent)
    db.commit()
    db.refresh(db_agent)
    return db_agent


def update_agent_definition(
    db: Session,
    agent_id: int,
    name: str | None = None,
    agent_type: models.AgentTypeEnum | None = None,
    description: str | None = None,
    config: dict | None = None,
    is_active: bool | None = None,
):
    agent = get_agent_definition(db, agent_id)
    if not agent:
        return None

    if name is not None:
        agent.actor.name = name
    if agent_type is not None:
        agent.agent_type = agent_type
    if description is not None:
        agent.description = description
    if config is not None:
        agent.config = config
    if is_active is not None:
        agent.is_active = is_active

    db.commit()
    db.refresh(agent)
    return agent


def delete_agent_definition(db: Session, agent_id: int):
    agent = get_agent_definition(db, agent_id)
    if not agent:
        return False
    db.delete(agent.actor)
    db.commit()
    return True


# ========== TaskAssignment Operations ==========


def get_task_assignment(db: Session, assignment_id: int):
    return db.query(models.TaskAssignment).filter(models.TaskAssignment.id == assignment_id).first()


def get_task_assignments(db: Session, task_id: int):
    return db.query(models.TaskAssignment).filter(models.TaskAssignment.task_id == task_id).all()


def get_actor_assignments(db: Session, actor_id: int, skip: int = 0, limit: int = 100):
    return (
        db.query(models.TaskAssignment)
        .filter(models.TaskAssignment.actor_id == actor_id)
        .offset(skip)
        .limit(limit)
        .all()
    )


def create_task_assignment(db: Session, task_id: int, actor_id: int, role: models.AssignmentRoleEnum):
    """Assign an actor to a task with a role. Idempotent on (task_id, actor_id,
    role): a repeat call returns the existing row instead of violating the
    unique constraint."""
    existing = (
        db.query(models.TaskAssignment)
        .filter(
            models.TaskAssignment.task_id == task_id,
            models.TaskAssignment.actor_id == actor_id,
            models.TaskAssignment.role == role,
        )
        .first()
    )
    if existing:
        return existing
    db_assignment = models.TaskAssignment(task_id=task_id, actor_id=actor_id, role=role)
    db.add(db_assignment)
    try:
        db.commit()
    except IntegrityError:
        # Only treat this as idempotent if a matching row now exists (a concurrent
        # insert won the unique-constraint race). Any other integrity error (e.g.
        # a bad task_id / actor_id FK) is re-raised rather than silently swallowed.
        db.rollback()
        winner = (
            db.query(models.TaskAssignment)
            .filter(
                models.TaskAssignment.task_id == task_id,
                models.TaskAssignment.actor_id == actor_id,
                models.TaskAssignment.role == role,
            )
            .first()
        )
        if winner is None:
            raise
        return winner
    db.refresh(db_assignment)
    return db_assignment


def delete_task_assignment(db: Session, assignment_id: int):
    assignment = get_task_assignment(db, assignment_id)
    if not assignment:
        return False
    db.delete(assignment)
    db.commit()
    return True


def delete_task_assignment_by_actor(db: Session, task_id: int, actor_id: int):
    assignment = (
        db.query(models.TaskAssignment)
        .filter(models.TaskAssignment.task_id == task_id, models.TaskAssignment.actor_id == actor_id)
        .first()
    )
    if not assignment:
        return False
    db.delete(assignment)
    db.commit()
    return True


# ========== Task Operations ==========


def get_task(db: Session, task_id: int):
    return db.query(models.Task).filter(models.Task.id == task_id).first()


def get_task_by_thread_id(db: Session, thread_id: str):
    return db.query(models.Task).filter(models.Task.thread_id == thread_id).first()


def list_tasks(
    db: Session,
    status: models.TaskStatusEnum | None = None,
    skip: int = 0,
    limit: int = 100,
):
    """List all tasks (single-user PoC)."""
    query = db.query(models.Task)
    if status:
        query = query.filter(models.Task.status == status)
    return query.order_by(models.Task.created_at.desc()).offset(skip).limit(limit).all()


def get_actor_tasks_by_role(
    db: Session,
    actor_id: int,
    role: models.AssignmentRoleEnum,
    status: models.TaskStatusEnum | None = None,
    skip: int = 0,
    limit: int = 100,
):
    """Get tasks where the given actor has the specified assignment role."""
    query = (
        db.query(models.Task)
        .join(models.TaskAssignment)
        .filter(models.TaskAssignment.actor_id == actor_id, models.TaskAssignment.role == role)
        # distinct(): defence in depth against a task appearing twice via the
        # join (the (task_id, actor_id, role) unique constraint should already
        # prevent duplicate assignments).
        .distinct()
    )
    if status:
        query = query.filter(models.Task.status == status)
    return query.order_by(models.Task.created_at.desc()).offset(skip).limit(limit).all()


def list_pending_approvals(db: Session, actor_id: int, skip: int = 0, limit: int = 100):
    """List tasks the given actor must approve."""
    return get_actor_tasks_by_role(
        db,
        actor_id=actor_id,
        role=models.AssignmentRoleEnum.APPROVER,
        status=models.TaskStatusEnum.WAITING_APPROVAL,
        skip=skip,
        limit=limit,
    )


def create_task(
    db: Session,
    thread_id: str,
    title: str,
    creator_actor_id: int | None = None,
    description: str | None = None,
):
    db_task = models.Task(
        thread_id=thread_id,
        title=title,
        creator_actor_id=creator_actor_id,
        description=description,
        status=models.TaskStatusEnum.DRAFT,
    )
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task


def update_task(
    db: Session,
    task_id: int,
    title: str | None = None,
    description: str | None = None,
    status: models.TaskStatusEnum | None = None,
    current_draft: str | None = None,
    feedback: str | None = None,
):
    task = get_task(db, task_id)
    if not task:
        return None

    if title is not None:
        task.title = title
    if description is not None:
        task.description = description
    if status is not None:
        task.status = status
    if current_draft is not None:
        task.current_draft = current_draft
    if feedback is not None:
        task.feedback = feedback

    db.commit()
    db.refresh(task)
    return task


def update_task_status(db: Session, task_id: int, status: models.TaskStatusEnum):
    task = get_task(db, task_id)
    if not task:
        return None
    task.status = status
    db.commit()
    db.refresh(task)
    return task


def delete_task(db: Session, task_id: int):
    task = get_task(db, task_id)
    if not task:
        return False
    db.delete(task)
    db.commit()
    return True


# ========== Ledger contract errors ==========


class LedgerError(ValueError):
    """A write that would violate the approval ledger's contract."""


class TaskNotFoundError(LedgerError):
    pass


class ArtifactRequiredError(LedgerError):
    """An approval was attempted on a task that has no artifact to bind to."""


class StaleArtifactError(LedgerError):
    """The decision targeted a draft version / commitment that is no longer the latest."""


class CommitmentMismatchError(LedgerError):
    """A supplied commitment does not match the artifact content."""


class DuplicateDecisionError(LedgerError):
    """This decision key is already recorded; `approval` is the entry that holds it.

    Raised rather than returned so a caller cannot mistake "somebody already
    wrote this" for "I wrote this". The difference decides who owns the work
    that follows the ledger write — resuming the graph — and two racing rounds
    both believing they wrote it is how one decision gets applied twice.
    """

    def __init__(self, approval) -> None:
        super().__init__(f"decision {approval.decision_key} is already recorded on task {approval.task_id}")
        self.approval = approval


def latest_approval(db: Session, task_id: int):
    """The newest approval entry on a task, or None — the head of its hash chain."""
    return (
        db.query(models.Approval).filter(models.Approval.task_id == task_id).order_by(models.Approval.id.desc()).first()
    )


def find_decision(db: Session, task_id: int, decision_key: str):
    """The approval already recorded under `decision_key`, or None.

    The read half of decision idempotency (migration 013). Callers that can be
    replayed - the 2026-07-28 multi-round `tools/call` re-sends its arguments
    every round - use this to tell "the decision was already made" apart from
    "make it again".
    """
    return (
        db.query(models.Approval)
        .filter(models.Approval.task_id == task_id, models.Approval.decision_key == decision_key)
        .one_or_none()
    )


def _lock_task(db: Session, task_id: int) -> models.Task:
    """Take the per-task row lock that serializes ledger writers.

    `with_for_update` is a no-op on SQLite (the test backend), which serializes
    writers at the database level anyway. On Postgres it is a real `SELECT ...
    FOR UPDATE`, held until the caller commits.
    """
    task = db.query(models.Task).filter(models.Task.id == task_id).with_for_update().first()
    if task is None:
        raise TaskNotFoundError(f"Task {task_id} not found")
    return task


# ========== Draft Operations ==========


def _latest_draft(db: Session, task_id: int) -> models.Draft | None:
    return db.query(models.Draft).filter(models.Draft.task_id == task_id).order_by(models.Draft.version.desc()).first()


def _append_draft(
    db: Session,
    task_id: int,
    content: str,
    *,
    producer_actor_id: int | None = None,
    commitment: str | None = None,
) -> models.Draft:
    """Append the next draft version. Caller holds the task lock; no commit here."""
    computed = ledger.compute_artifact_commitment(content)
    if commitment is not None and commitment != computed:
        raise CommitmentMismatchError("Supplied commitment does not match the draft content")
    last = _latest_draft(db, task_id)
    draft = models.Draft(
        task_id=task_id,
        version=(last.version + 1) if last else 1,
        content=content,
        commitment=computed,
        commitment_algorithm=ledger.COMMITMENT_ALGORITHM,
        producer_actor_id=producer_actor_id,
    )
    db.add(draft)
    db.flush()
    return draft


def add_draft(
    db: Session,
    task_id: int,
    content: str,
    *,
    producer_actor_id: int | None = None,
    commitment: str | None = None,
):
    """Append a new draft version to a task, with its content commitment.

    `commitment`, when given, is a source-produced value that must match the
    content; a mismatch is refused rather than silently recomputed. The task
    row is locked first so two appends cannot claim the same version (the
    unique constraint would catch the second one, but the lock avoids the
    failed transaction altogether).
    """
    _lock_task(db, task_id)
    draft = _append_draft(db, task_id, content, producer_actor_id=producer_actor_id, commitment=commitment)
    db.commit()
    db.refresh(draft)
    return draft


def get_drafts(db: Session, task_id: int):
    return db.query(models.Draft).filter(models.Draft.task_id == task_id).order_by(models.Draft.version.asc()).all()


# ========== Approval Operations ==========


def record_approval(
    db: Session,
    task_id: int,
    reviewer_actor_id: int | None,
    action: str,
    comment: str | None = None,
    *,
    artifact_version: int | None = None,
    expected_commitment: str | None = None,
    modified_draft: str | None = None,
    decision_key: str | None = None,
):
    """Record an approval / rejection event bound to the artifact it decided on.

    The entry is chained to the task's prior entry (see app/ledger.py) and
    carries an **artifact binding**: the reference, version, commitment and
    producer of the draft that was reviewed. All of it is inside the hash.

    * A task with no draft cannot be approved (`ArtifactRequiredError`): an
      approval must have an unambiguous target.
    * `artifact_version` / `expected_commitment` let the caller say which
      draft they looked at. If the task's latest draft is no longer that one,
      nothing is recorded (`StaleArtifactError`) — the operator never approves
      content they did not see. Both are optional so trusted local callers can
      approve "whatever is current".
    * `modified_draft` appends a new draft version (producer = the reviewer)
      **before** the approval is written, and the approval binds to that new
      version.
    * `decision_key` makes the write idempotent: if this task already carries
      an approval under that key, `DuplicateDecisionError` is raised carrying
      that entry and **nothing is appended** - not the approval, and not the
      `modified_draft` version it would have created. The lookup happens under
      the task row lock, so of two racing rounds exactly one is told it wrote;
      migration 013's unique index is the backstop if they reach the table on
      separate connections anyway.

    created_at is set explicitly here (not via the column default) so the value
    that is hashed is exactly the value persisted.

    **Concurrency**: read-prev-then-append is a read-modify-write on the task's
    hash chain, so two approvals racing on one task would otherwise both chain
    off the same `prev_hash` and fork the chain (`verify_approval_chain` would
    then flag the later one as tampering). The task row lock taken first
    serializes writers per task for the rest of the transaction — draft append
    included, so the version an approval binds to cannot be raced either. This
    is what makes the ledger safe for the multi-actor / multi-device use the
    coordination layer enables (docs/delta-mvp-spec.md §11.6).
    """
    # Lock the task row *before* reading drafts or the chain head, so a
    # concurrent writer on the same task waits here rather than racing us.
    _lock_task(db, task_id)

    # Idempotency first: a replay must not append a draft version either, so
    # this runs before `modified_draft` is considered and before the staleness
    # checks, which the replay would fail once its own decision moved the task on.
    if decision_key is not None:
        already = find_decision(db, task_id, decision_key)
        if already is not None:
            raise DuplicateDecisionError(already)

    shown = _latest_draft(db, task_id)
    if shown is None:
        raise ArtifactRequiredError(f"Task {task_id} has no draft; an approval must bind to an artifact")
    if artifact_version is not None and shown.version != artifact_version:
        raise StaleArtifactError(
            f"Task {task_id}: the decision targeted draft v{artifact_version} but the latest is v{shown.version}"
        )
    # A row the 009 backfill did not reach (SQLite test schema, or content
    # added outside the app) has no commitment yet; compute it in memory so
    # the checks below run on it, and persist it only once they pass.
    shown_commitment = shown.commitment or ledger.compute_artifact_commitment(shown.content)
    if expected_commitment is not None and shown_commitment != expected_commitment:
        raise StaleArtifactError(
            f"Task {task_id}: draft v{shown.version} no longer has the commitment the decision targeted"
        )
    if shown.commitment is None:
        shown.commitment = shown_commitment
        shown.commitment_algorithm = ledger.COMMITMENT_ALGORITHM
        db.flush()

    target = shown
    if modified_draft is not None:
        # Always a new version, even for identical text: the graph appends
        # the modified draft to its own state unconditionally, and the
        # projection matches versions by content, so the two stay aligned.
        target = _append_draft(db, task_id, modified_draft, producer_actor_id=reviewer_actor_id)

    # Stamped after the artifact exists: the approval is the later event.
    now = datetime.utcnow()
    binding = ledger.ArtifactBinding(
        ref=ledger.artifact_ref(task_id, target.version),
        version=target.version,
        commitment=target.commitment,
        commitment_algorithm=target.commitment_algorithm,
        producer_actor_id=target.producer_actor_id,
    )

    last = latest_approval(db, task_id)
    prev_hash = last.entry_hash if last else None
    entry_hash = ledger.compute_entry_hash(
        prev_hash,
        task_id=task_id,
        reviewer_actor_id=reviewer_actor_id,
        action=action,
        comment=comment,
        created_at=now,
        artifact=binding,
        decision_key=decision_key,
    )
    db_approval = models.Approval(
        task_id=task_id,
        reviewer_actor_id=reviewer_actor_id,
        action=action,
        comment=comment,
        created_at=now,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
        hash_version=ledger.ENTRY_HASH_VERSION,
        artifact_ref=binding.ref,
        artifact_version=binding.version,
        artifact_commitment=binding.commitment,
        artifact_commitment_algorithm=binding.commitment_algorithm,
        producer_actor_id=binding.producer_actor_id,
        decision_key=decision_key,
    )
    db.add(db_approval)
    db.commit()
    db.refresh(db_approval)
    return db_approval


def get_approvals(db: Session, task_id: int):
    return db.query(models.Approval).filter(models.Approval.task_id == task_id).order_by(models.Approval.id.asc()).all()


def verify_approval_chain(db: Session, task_id: int) -> dict:
    """Recompute the approval hash chain for a task and report tampering.

    Returns ``{"valid", "broken_at", "count", "legacy", "artifact_bound",
    "unbound"}``:

    * ``legacy`` — pre-migration-004 rows (entry_hash IS NULL) that predate the
      hash chain. Not covered by tamper-evidence; skipped as a leading prefix.
    * ``artifact_bound`` — rows hashed with the v2 payload, i.e. whose entry
      names the exact draft (ref, version, commitment, producer) it approved.
    * ``unbound`` — every other row: legacy rows plus v1 rows (004–008), which
      are tamper-evident as events but do not identify their artifact.

    A mismatch among hashed rows means a recorded approval was altered or
    reordered — including any of the artifact-binding fields, or the
    ``hash_version`` marker itself, since the version is inside the hash.
    """
    approvals = get_approvals(db, task_id)
    prev_hash = None
    legacy = 0
    bound = 0
    seen_hashed = False

    def _report(valid: bool, broken_at: int | None) -> dict:
        return {
            "valid": valid,
            "broken_at": broken_at,
            "count": len(approvals),
            "legacy": legacy,
            "artifact_bound": bound,
            "unbound": len(approvals) - bound,
        }

    for approval in approvals:
        # Before the legacy skip, because a pre-004 row has no hash to break
        # and would otherwise be the one place a key could be planted for
        # free. A key on any payload below v3 is not covered by that row's
        # hash - see the dispatch below - and the unique index would then
        # refuse the genuine decision the key belongs to.
        if approval.decision_key is not None and (approval.hash_version or 1) < ledger.DECISION_KEY_SINCE:
            return _report(False, approval.id)
        if approval.entry_hash is None:
            # A NULL hash is only acceptable as a leading legacy prefix (rows that
            # predate the hash chain). A NULL appearing *after* the chain has
            # started means a hashed row was blanked out — that is tampering.
            if seen_hashed:
                return _report(False, approval.id)
            legacy += 1
            continue
        seen_hashed = True
        artifact = None
        # Dispatch on the row's own version (NULL/1 = event-only payload), not
        # on equality with the current version, so a later payload bump does
        # not turn every older row into a false tamper report.
        if (approval.hash_version or 1) >= ledger.ARTIFACT_BINDING_SINCE:
            artifact = ledger.ArtifactBinding(
                ref=approval.artifact_ref,
                version=approval.artifact_version,
                commitment=approval.artifact_commitment,
                commitment_algorithm=approval.artifact_commitment_algorithm,
                producer_actor_id=approval.producer_actor_id,
            )
        expected = ledger.compute_entry_hash(
            prev_hash,
            task_id=approval.task_id,
            reviewer_actor_id=approval.reviewer_actor_id,
            action=approval.action,
            comment=approval.comment,
            created_at=approval.created_at,
            artifact=artifact,
            version=approval.hash_version,
            # Dispatched the same way: a v2 row is recomputed without the field
            # rather than with a null one, so it verifies as it was written.
            decision_key=approval.decision_key,
        )
        if approval.prev_hash != prev_hash or approval.entry_hash != expected:
            return _report(False, approval.id)
        if artifact is not None:
            bound += 1
        prev_hash = approval.entry_hash
    return _report(True, None)
