# Chrome Evidence Clip experiment

This unpacked Chrome extension captures only text the operator explicitly
selects. It can optionally annotate that excerpt with Chrome's built-in Prompt
API, shows the result for review, then writes the clip to one AxonRelay task.

It does not collect browsing history, fetch a URL from the backend, run tools
from model output, or share clips across users.

## Run

1. Set a long random `AXONRELAY_CAPTURE_TOKEN` for the backend and start it on
   `127.0.0.1:8000`.
2. Open `chrome://extensions`, enable Developer mode, and load this directory
   as an unpacked extension.
3. Configure the same capture token in the popup.
4. Select public or personal text in an HTTP(S) page, open the popup, and load
   the selection. Review it before saving. AI annotation is optional.

Add workplace or otherwise prohibited domain suffixes to **Blocked host
suffixes**. Workplace data remains out of scope even if the extension could
technically access it.

The capture token is stored in `chrome.storage.local`; this is acceptable only
for the personal PoC. A production extension needs OS-backed secret handling or
a short-lived pairing flow.
