"""AxonRelay LangGraph definition deployed to LangGraph Platform.

Flow:
    START -> writer -> reviewer -> [interrupt: human_approval] -> approver_route
                                                                    |
                                                          approved -+-> finalize -> END
                                                          rejected -+-> writer (revision loop)

The interrupt at human_approval pauses the thread until the Backend resumes
it with a decision payload via the LangGraph SDK.
"""

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent.llm import reviewer_llm, writer_llm
from agent.state import AgentState

MAX_REVISIONS = 3

WRITER_SYSTEM = (
    "You are AxonRelay's writer agent. Produce a clear, concise draft for the "
    "user's task. Treat reviewer comments and prior drafts as authoritative "
    "guidance for revisions. Output the draft only, with no preamble."
)

REVIEWER_SYSTEM = (
    "You are AxonRelay's reviewer agent. Read the latest draft and produce "
    "specific, actionable feedback. Be terse: bullet points only, max 5. If "
    "the draft is acceptable, output 'LGTM' and nothing else."
)


async def writer_node(state: AgentState) -> dict:
    title = state.get("title", "")
    description = state.get("description", "") or ""
    drafts = state.get("drafts", [])
    reviewer_comments = state.get("reviewer_comments", [])
    iteration = state.get("iteration", 0)

    parts = [f"# Task\n{title}"]
    if description:
        parts.append(f"\n## Description\n{description}")
    if drafts:
        parts.append(f"\n## Previous draft\n{drafts[-1]}")
    if reviewer_comments:
        parts.append(f"\n## Reviewer feedback\n{reviewer_comments[-1]}")
    if state.get("human_comment"):
        parts.append(f"\n## Human revision request\n{state['human_comment']}")

    response = await writer_llm().ainvoke(
        [SystemMessage(content=WRITER_SYSTEM), HumanMessage(content="\n".join(parts))]
    )
    new_draft = response.content if isinstance(response.content, str) else str(response.content)

    return {
        "drafts": drafts + [new_draft],
        "iteration": iteration + 1,
        # Clear stale human inputs after consuming them
        "human_comment": None,
        "modified_draft": None,
    }


async def reviewer_node(state: AgentState) -> dict:
    drafts = state.get("drafts", [])
    if not drafts:
        return {"reviewer_comments": ["No draft to review."]}

    latest = drafts[-1]
    response = await reviewer_llm().ainvoke(
        [
            SystemMessage(content=REVIEWER_SYSTEM),
            HumanMessage(
                content=(
                    f"# Task\n{state.get('title', '')}\n\n"
                    f"## Draft to review\n{latest}"
                )
            ),
        ]
    )
    comment = response.content if isinstance(response.content, str) else str(response.content)
    reviewer_comments = state.get("reviewer_comments", [])
    return {"reviewer_comments": reviewer_comments + [comment]}


def human_approval_node(state: AgentState) -> dict:
    """Pause the thread waiting for a human decision.

    The interrupt payload exposed to the Backend / MCP / Discord:
        - task_id
        - latest draft
        - latest reviewer comment

    The Backend resumes with one of:
        {"decision": "approved", "human_comment": "...", "modified_draft": "..."}
        {"decision": "rejected", "human_comment": "..."}
    """
    drafts = state.get("drafts", [])
    reviewer_comments = state.get("reviewer_comments", [])

    payload = interrupt(
        {
            "task_id": state.get("task_id"),
            "latest_draft": drafts[-1] if drafts else None,
            "latest_reviewer_comment": reviewer_comments[-1] if reviewer_comments else None,
            "iteration": state.get("iteration", 0),
        }
    )

    decision = payload.get("decision") if isinstance(payload, dict) else None
    human_comment = payload.get("human_comment") if isinstance(payload, dict) else None
    modified_draft = payload.get("modified_draft") if isinstance(payload, dict) else None

    update: dict = {"decision": decision, "human_comment": human_comment}

    # If the human edited the draft directly, append it as the latest version
    # so the finalize node uses it.
    if modified_draft:
        update["drafts"] = drafts + [modified_draft]
        update["modified_draft"] = modified_draft

    return update


def route_after_approval(state: AgentState) -> str:
    if state.get("decision") == "approved":
        return "finalize"
    if state.get("iteration", 0) >= MAX_REVISIONS:
        # Hard cap on revision loops; finalize with the latest draft anyway.
        return "finalize"
    return "writer"


async def finalize_node(state: AgentState) -> dict:
    drafts = state.get("drafts", [])
    return {"final_output": drafts[-1] if drafts else None}


def build_graph() -> StateGraph:
    builder = StateGraph(AgentState)
    builder.add_node("writer", writer_node)
    builder.add_node("reviewer", reviewer_node)
    builder.add_node("human_approval", human_approval_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "writer")
    builder.add_edge("writer", "reviewer")
    builder.add_edge("reviewer", "human_approval")
    builder.add_conditional_edges(
        "human_approval",
        route_after_approval,
        {"finalize": "finalize", "writer": "writer"},
    )
    builder.add_edge("finalize", END)

    return builder


graph = build_graph().compile()
