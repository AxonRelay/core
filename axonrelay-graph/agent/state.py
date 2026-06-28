"""LangGraph State schema for AxonRelay agent.

The graph runs writer -> reviewer -> [human approval] -> finalize, with a
revision loop back to writer when the human rejects.
"""

from typing import TypedDict


class AgentState(TypedDict, total=False):
    # Identity / inputs
    task_id: int
    title: str
    description: str | None

    # Accumulated outputs
    drafts: list[str]
    reviewer_comments: list[str]

    # Human-in-the-loop decision (set after interrupt resume)
    decision: str | None  # "approved" | "rejected" | None
    human_comment: str | None
    modified_draft: str | None

    # Final state
    final_output: str | None
    iteration: int  # revision count (for cycle limit)
