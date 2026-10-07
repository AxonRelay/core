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
