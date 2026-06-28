# axonrelay-graph

LangGraph Platform deployment package for the AxonRelay agent.

## Graph

```
START -> writer -> reviewer -> [human_approval interrupt] -> approver_route
                                                              |
                                                  approved -> finalize -> END
                                                  rejected -> writer (revision loop, capped at 3)
```

State schema: see [agent/state.py](agent/state.py).

## Local development

```bash
cp .env.example .env
# Fill in ANTHROPIC_API_KEY (or set LLM_PROVIDER=openai + OPENAI_API_KEY)
pip install -e .
langgraph dev   # starts a local LangGraph Server with Studio
```

The Studio UI lets you submit a thread input like:
```json
{
  "task_id": 1,
  "title": "Compare X vs Y libraries",
  "description": "Pick one for the next release"
}
```

When the graph hits the `human_approval` interrupt, resume it from Studio (or
later, from the AxonRelay backend) with:
```json
{
  "decision": "approved",
  "human_comment": "LGTM",
  "modified_draft": null
}
```

or

```json
{
  "decision": "rejected",
  "human_comment": "Needs more detail on X performance"
}
```

## Deploy to LangGraph Platform

```bash
langgraph build
# Then deploy via Platform UI or `langgraph deploy` (depending on plan).
```

## Integration

The AxonRelay backend talks to the deployed graph via [langgraph_sdk](https://pypi.org/project/langgraph-sdk/).
See `backend/app/langgraph_client.py`.
