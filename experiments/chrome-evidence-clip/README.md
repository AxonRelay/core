# Chrome Evidence Clip experiment

This unpacked Chrome extension captures only text the operator explicitly
selects. It can optionally annotate the excerpt with Chrome's built-in Prompt
API, shows the result for review, and then records a **commitment** to the
excerpt on one AxonRelay task ([ADR-014](../../docs/adr-014-evidence-clips.md)).

What is sent and what stays:

| Sent to AxonRelay | Stays in this Chrome profile |
|---|---|
| SHA-256 of the exact (trimmed) excerpt | the excerpt |
| URL without query, fragment or credentials | the full URL, the page title |
| `public` / `personal` | AI annotations |

The extension does not collect browsing history, AxonRelay never fetches the
URL, and nothing in the model output can choose the task, the URL or any action.

## Run

1. Start the backend on `127.0.0.1:8000`. If the instance enforces
   credentials (`AXONRELAY_REQUIRE_AUTH=1`), issue one with `ledger:write`
   (ADR-011) and paste it into **Credential**.
2. Open `chrome://extensions`, enable Developer mode, and load this directory
   as an unpacked extension.
3. Select public or personal text on an HTTP(S) page, open the popup, load
   the selection, review it, and record it. AI annotation is optional.
4. Click **Export local clips (JSONL)** and load the file on the machine where
   your agent runs:

   ```bash
   cd backend && python -m app.evidence_local import ~/Downloads/axonrelay-evidence.jsonl
   ```

   The import refuses any line whose text does not hash to its stated digest.
   An agent connected over stdio then gets the excerpts from `get_context_pack`.
   Over HTTP the pack lists the clips as `unresolved`, because the server's
   disk is not where your text is.

Add workplace or otherwise prohibited domain suffixes to **Blocked host
suffixes**. Workplace data remains out of scope even if the extension could
technically access it.

The credential is stored in `chrome.storage.local`; this is acceptable only
for the personal PoC. A production extension needs OS-backed secret handling or
a short-lived pairing flow.

`evaluation-template.csv` is the scoring sheet for the PoC in issue #52.

### Compare decision time and quality

Copy the template locally and replace its example row with observations. Use
A for your usual workflow, B for raw clips and C for annotated clips. Record
a positive decision time in seconds and have a reviewer score the decision
from 1 to 5 without knowing the condition. Give every trial in a batch a
unique task ID. For each B or C trial, enter the comparable A task ID from
the same batch and task type in `baseline_pair_task_id`. Choose pairs before
scoring. Each A trial can serve once per comparison arm.

From this directory run `python3 evaluate.py /path/to/trials.csv` with Python 3.12.
For B and C separately, the report shows the number of matched pairs, the
median of their time reductions (100 × (1 − trial time / baseline time)),
the mean trial-minus-baseline quality score, and how many A trials were left
unpaired for each comparison. Positive values indicate
faster decisions or higher quality. A missing arm produces zero pairs and
null estimates. The output contains no task IDs, excerpts, or notes.

### Measure retrieval and citations

For B and C, choose the relevant IDs before running the query and record them
in `expected_relevant_ids`. Copy up to five IDs from the result into
`returned_top5_ids`. Record the distinct IDs cited in the draft in `cited_ids`
and copy the distinct missing IDs from `validate_evidence_references` into
`missing_or_invalid_ids`. Enter IDs as `E-1 E-2` or `[E-1], [E-2]`. Copy each
ID as written, including leading zeros on invalid citations. Use `-` for an
observed empty list and leave both cells blank
when a measurement was skipped.

The same command reports Recall@5 as retrieved relevant IDs divided by expected
IDs, Relevant@5 as retrieved relevant IDs divided by returned IDs, and citation
integrity as valid cited IDs divided by all cited IDs. Each ratio pools counts
across measured trials within its condition. A zero denominator produces
`null`. Drafts with no citations are counted separately. The report includes
aggregate counts only.

Small samples and arms paired to different baselines limit comparisons between
B and C. Record reuse and privacy observations separately as described in
[issue #52](https://github.com/AxonRelay/core/issues/52). Keep the completed
worksheets local.
