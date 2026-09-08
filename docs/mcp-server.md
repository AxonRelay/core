# AxonRelay MCP Server

AxonRelay は **MCP サーバ** として動作し、Claude Code / Cursor / Zed などの IDE から直接タスク作成・承認・差戻しと、複数エージェント間の調整ができる。

> **SDK バージョン**: `mcp>=2.1,<3`。2.0 で `FastMCP` が `MCPServer`（`mcp.server.mcpserver`）に改称されたため、requirements.txt でピン止めしている。

## 起動

### stdio transport（IDE から直接起動 — ローカル1台なら推奨）

```bash
cd backend
python -m app.mcp.server
```

### Streamable HTTP transport（複数デバイスで1インスタンスを共有する場合）

```bash
cd backend
python -m app.mcp.server --http --port 8765
```

調整レイヤーは **全員が同じインスタンスを見ていること**が前提なので、デバイスが複数あるならこちらを使う。既定のバインドは loopback。**既定では呼び出し元認証がない**ので、他デバイスからの到達は Tailscale か Cloudflare Tunnel 経由にする（**[deploy/DEPLOYMENT.ja.md](../deploy/DEPLOYMENT.ja.md)** に手順）。公開バインドは想定外。

`AXONRELAY_MCP_TOKEN` を設定すると、全リクエストに `Authorization: Bearer <token>` を要求する（[ADR-008](./adr-008-optional-bearer-token.md)）。クライアントは `claude mcp add -t http ... -H "Authorization: Bearer <token>"` のように固定ヘッダで渡す。

クライアント側の設定例：

```json
{
  "mcpServers": {
    "axonrelay": {
      "type": "http",
      "url": "http://<tailscale-hostname>:8765/mcp"
    }
  }
}
```

### Claude Code の設定

リポジトリ直下に project scope の **[`.mcp.json`](../.mcp.json)** を同梱している。venv を
有効にしたシェルで、リポジトリのルートから `claude` を起動すると、初回に `axonrelay`
サーバを有効にするか聞かれる。`.env` は自動で読まれるので、`DATABASE_URL` を別途渡す
必要はない。エントリは `backend/` で `python -m app.mcp.server` を実行するだけ。

リポジトリの外から使う、または user scope に置くなら `claude mcp add`:

```bash
claude mcp add -s user axonrelay \
  -e DATABASE_URL=postgresql://axonrelay:axonrelay_dev@localhost:5432/axonrelay \
  -- bash -c 'cd /path/to/core/backend && exec python -m app.mcp.server'
```

複数デバイスで 1 つの HTTP transport を共有する場合（[deploy/DEPLOYMENT.ja.md](../deploy/DEPLOYMENT.ja.md) §3）:

```bash
claude mcp add -s user -t http axonrelay http://<host>.<tailnet>.ts.net:8765/mcp
```

設定先は `.mcp.json`（project）か `claude mcp add`（local / user）のどちらかで、
`~/.claude/settings.json` は MCP サーバの設定を読まない。

> LangGraph Platform を使うときは `.env` の `LANGGRAPH_API_URL` / `LANGGRAPH_API_KEY` /
> `LANGGRAPH_ASSISTANT_ID` を埋める。未設定でも調整系 tool と読み取り系 tool は動く。

## 提供する Tools

| Tool | 用途 |
|---|---|
| `list_tasks(status?, limit?)` | タスク一覧 |
| `list_pending_approvals()` | **統一承認受信箱** — 最も重要 |
| `get_task(task_id)` | タスク詳細（assignments / drafts / approvals 同梱） |
| `get_drafts(task_id)` | ドラフト履歴（各版の `commitment` = 本文 SHA-256 と `producer_actor_id` を含む） |
| `create_task(title, description?, assignments?)` | 新規タスク + Platform thread 確保 |
| `run_task(task_id)` | Platform 上で graph 実行 → 承認待ちまで |
| `approve_task(task_id, comment?, modified_draft?, artifact_version?, expected_commitment?)` | 承認（任意で edit）。`artifact_version` / `expected_commitment` を渡すと**読んだ版に束縛**され、task が先に進んでいれば何も記録せず `status="stale_decision"` を返す。`modified_draft` は新しい draft 版として保存され、承認はその版に束縛される。戻り値に `approval`（束縛と hash を含む）を同梱 |
| `reject_task(task_id, comment?, reason?, artifact_version?, expected_commitment?)` | 差戻し（revision loop）。束縛引数の意味は `approve_task` と同じ |
| `review_pending_task(task_id)` | **対話的承認** — MCP `elicitation` でドラフトを提示し承認/差戻しを尋ね、台帳記録 + resume まで一括。問いの運び方は交渉した改訂に従う（下の[互換性マトリクス](#mcp-互換性マトリクス)）。表示した draft の版と commitment に判断を束縛するので、待っている間に draft が差し替わると**もう一度尋ねられる**（新しい draft について）。task が承認待ちを離れていれば `stale_decision`。form elicitation 非対応のクライアントには何も尋ねず、`status="elicitation_unsupported"` と束縛引数つきで `approve_task`/`reject_task` を案内する |
| `verify_task_ledger(task_id)` | 承認 hash chain の改ざん検証（`{valid, broken_at, count, legacy, artifact_bound, unbound}`。`legacy` は hash chain 導入前の行数、`artifact_bound` は判断対象の draft 版と commitment を名指しする行数、`unbound` はそれ以外。[ADR-009](./adr-009-artifact-commitment.md)） |
| `list_agents(agent_type?, is_active?)` | AI Actor 一覧 |
| `create_agent(name, agent_type, ...)` | AI Actor 定義 |
| `update_agent(agent_id, ...)` | AI Actor 更新 |
| `get_self_actor()` | オペレータ Human Actor |

## MCP 互換性マトリクス

`backend/app/mcp/compat.py` が原本で、`axonrelay://compat` として配信される。
テストが SDK の対応版集合と突き合わせるので、この表と実装はずれない
（[ADR-013](./adr-013-mcp-2026-interaction.md)）。

| 仕様改訂 | 承認の問いの運び方 |
|---|---|
| `2024-11-05` / `2025-03-26` | elicitation なし。承認は `approve_task` / `reject_task` |
| `2025-06-18` / `2025-11-25` | `tools/call` の中で単発の `elicitation/create`。接続を開いたまま待つ |
| `2026-07-28` | 多ラウンド `tools/call`。`InputRequiredResult` + `requestState` を返し、再接続しても再開できる |

- **Python SDK**: `mcp>=2.1,<3`（`backend/requirements.txt` と同一。上限は手で動かす）
- **検証済みクライアント**: Claude Code（stdio / `.mcp.json`、form elicitation）、
  SDK 自身の `ClientSession`（stdio と Streamable HTTP の両方で、両モデルを CI で）
- **fallback**: `approve_task` / `reject_task`。`artifact_version` /
  `expected_commitment` を明示に取るので、対話ラウンドなしで同じ束縛が得られる

### 再開ハンドル（2026-07-28）

未完了の承認は `requestState` として封をしてクライアントに返り、次の `tools/call`
で戻ってくる。SDK は自分が発行したものだけを受け入れる。

- 既定の鍵はプロセスローカル。再起動をまたぐと問い直しになる（stdio ではこれが正しい）
- `AXONRELAY_REQUEST_STATE_KEY`（32 バイト以上）を設定すると、Streamable HTTP の
  複数ワーカーと再起動をまたいで再開できる。弱い鍵は起動時に名指しで拒否される
- TTL は 15 分
- ハンドルは発行した呼び出し元（提示された credential）に束縛される。stdio と
  無提示の HTTP では無束縛で、これは loopback 前提と同じ
- 同じラウンドの再送は台帳側で吸収される（migration 013）。承認も、編集が作る
  はずだった draft 版も二重には入らない。キーには reviewer も含まれるので、
  別の承認権者が同じ判断に至っても別のエントリになる
- `decision_key` は hash payload v3 の中にある（[ADR-013](./adr-013-mcp-2026-interaction.md)）。
  サーバが動作の根拠にする値なので、検証が見られない場所には置かない
- 判断が通って task が承認待ちを離れたあとの再送は `stale_decision`。文面は
  「このラウンドは何も記録していない」とだけ言い、立っている判断は
  `get_task` / `verify_task_ledger` で読めと案内する
- 配送済みの判断と**同じ draft・同じ文言**の判断は再送と区別できないため拒否され、
  `replayed: true` と `note`（`approve_task` / `reject_task` を使えという案内）が返る
- **graph への配送はやり直さない**。2 回目の配送は、そのとき graph が到達している
  interrupt に答えてしまう。確認が取れなかった場合は `ToolError` で「台帳には記録済み・
  graph は未確認・送り直すなら `approve_task` / `reject_task`」と返る

### 呼び出し元 identity と scope（任意）

`AXONRELAY_REQUIRE_AUTH=1` のインスタンスでは、HTTP transport の全呼び出しに credential が要る（[ADR-011](./adr-011-caller-identity.md)）。stdio は常に loopback 信頼で、フラグの有無にかかわらず credential は不要。

```bash
python -m app.credentials issue --actor self --scopes ledger:read,ledger:write,coordination:read,coordination:write
# → token は一度だけ表示される。保存は SHA-256 のみ
claude mcp add -t http -H "Authorization: Bearer <token>" axonrelay http://127.0.0.1:8765/mcp
```

各 tool に必要な scope は `backend/app/authz.py` の `TOOL_SCOPES`。読み取り系は `ledger:read` / `coordination:read`、書き込み系は `ledger:write` / `coordination:write`、削除は `administration`。scope が足りない呼び出しは「必要な scope 名」だけを返して拒否される。`get_self_actor()` は**サーバから見た呼び出し元の Actor** を返すので、credential がどの identity に結びついているかはこれで確認できる。

### 構造化コードと開示ポリシー

調整系 tool は自由文に加えて**構造化コード**を受け取る（[ADR-012](./adr-012-metadata-minimization.md)）:
`register_session` / `heartbeat_session` の `focus_code`、`claim_territory` /
`claim_git_resource` の `reason_code`、`send_relay` の `code`、`ack_relay` の `ack_code`。
コードを付けておくと、共有インスタンスがその session / claim / relay について
**言えること**が増える——自由文は開示されないが、コードは開示される。

`AXONRELAY_SAFE_MODE=1` のインスタンスでは、ボード・claim・relay・衝突・git guard の
レスポンスが opaque ref とコードと件数だけになり、ホスト名・絶対パス・git ディレクトリ・
自由文は返らない。`repo` slug は `AXONRELAY_SAFE_PUBLIC_IDENTIFIERS=1` のときだけ返る。
Actor 名も `actor_ref` に置き換わり、これは MCP と REST の両方で同じである。

> 注意: safe mode では調整系の**書き込み** tool 自体が拒否されるので（[ADR-010](./adr-010-safe-envelope.md)）、
> コードを設定できるのは full-text モードだけである。safe mode でコードを送る producer 側の
> 配線は #31 の担当。

モードによらず、agent の `config` は**キー名のみ**（`config_keys`）が返る。

### Safe Envelope（content-blind 取り込み）

`AXONRELAY_SAFE_MODE=1` のインスタンスでは上の書き込み系 tool と調整レイヤーの書き込み系 tool は固定文言で拒否され、以下だけが書き込み経路になる（[ADR-010](./adr-010-safe-envelope.md)、[schema](./schemas/safe-envelope-v1.json)）。

| Tool | 用途 |
|---|---|
| `ingest_safe_envelope(envelope)` | metadata-only の envelope を 1 件取り込む。opaque id・action/outcome enum・producer 算出の artifact commitment・timestamp のみ。未知フィールドは拒否、拒否理由はフィールド名だけ。同じ `event_id` の再送は冪等 |
| `list_safe_events(limit?, action?)` | 取り込み済み envelope を新しい順に返す |

### 調整レイヤー (Phase 3)

複数デバイス・複数リポジトリ・同一リポジトリの複数 clone で、複数のエージェントが並行作業するための tool 群。設計は [coordination-spec.md](./coordination-spec.md)。

| Tool | 用途 |
|---|---|
| `register_session(actor_name, host, repo, clone_path, branch?, focus?)` | ボードに参加し `session_id` を得る。冪等 — 同じ clone の同じ actor は既存セッションを再開 |
| `heartbeat_session(session_id, focus?, branch?)` | 生存報告 + 「いま何をしているか」の更新。30分無音で stale 表示になる |
| `end_session(session_id)` | 離脱。保持中の claim をすべて解放 |
| `get_board(repo?)` | **1コールで全体像** — 稼働セッション / 有効な claim / 未 ack の relay |
| `check_conflicts(repo, paths, session_id?, mode?)` | claim せずに衝突だけ問い合わせる |
| `claim_territory(session_id, paths, reason?, mode?, ttl_minutes?, force?)` | 編集するパスの助言的リースを取る。**衝突時は拒否**し、保持者（誰が / どのマシン / どの clone）を返す |
| `release_territory(claim_id? \| session_id?)` | claim を返す |
| `send_relay(from_session_id, subject, body?, kind?, to_actor_id? \| to_workspace_id? \| to_repo?)` | 永続メッセージを投函。宛先未指定は全体ブロードキャスト |
| `read_inbox(session_id, include_acked?, limit?)` | 自分宛を読む（既読になる。ack するまで残る） |
| `ack_relay(relay_id, session_id, note?)` | 対応済みにする。受信者ごとなので他人の受信箱からは消えない |

## 提供する Resources

| URI | 内容 |
|---|---|
| `axonrelay://board` | 調整ボードのスナップショット（稼働セッション / claim / 未 ack relay）。tool call を消費せず ambient context に置ける |
| `axonrelay://compat` | 対応する MCP 仕様改訂・SDK 版・検証済みクライアント・fallback tool（下の[互換性マトリクス](#mcp-互換性マトリクス)と同じ内容を JSON で） |
| `axonrelay://tasks/{task_id}` | Markdown 形式のタスク詳細（LLM コンテキスト用） |
| `axonrelay://tasks/{task_id}/drafts/{version}` | 特定バージョンのドラフト |

## 典型的な dogfood セッション

Claude Code のチャットで：

```
> AxonRelay でタスク投入：「X ライブラリと Y ライブラリの比較」
[create_task が呼ばれる] → task_id=1 が返る

> task 1 を実行して
[run_task(1)] → writer + reviewer が走り、status=waiting_approval になる
                 → 承認待ちの draft が返る

> draft 内容を見せて
[get_drafts(1)] → v1 の本文を表示

> 第 3 段落だけ書き直して承認したい。修正内容は…
[approve_task(1, modified_draft="...修正後本文...")] → status=completed

> 今承認待ちのタスクある？
[list_pending_approvals()] → 一覧表示
```

## 典型的な調整セッション

複数 clone で並行作業しているとき、Claude Code のチャットで：

```
> AxonRelay に参加して。この clone は AxonRelay/core、ブランチは feature/x
[register_session(...)] → session_id=3

> いま他に誰か動いてる？
[get_board(repo="AxonRelay/core")]
  → codex が studio 上の /Users/dev/clones/core-2 で feature/b、
     focus="rewriting migration chain"、backend/alembic を exclusive で claim 中

> backend/app を触りたい
[claim_territory(3, ["backend/app"], reason="crud のリファクタ")]
  → granted: true（alembic とは重ならない）

> migration も触る必要が出てきた
[claim_territory(3, ["backend/alembic"])]
  → granted: false
     conflicts[0].holder = { actor: "codex", host: "studio", focus: "rewriting migration chain" }

> codex に聞いて
[send_relay(3, to_actor_id=<codex>, kind="question",
            subject="alembic head をどう扱う予定？", body="...")]

> 何か来てる？
[read_inbox(3)] → codex からの answer
```

## 今後の拡張

- Streamable HTTP transport を FastAPI 本体にマウント（`/mcp` エンドポイント）して1プロセス化する
- Discord / モバイル経由の承認も同じ tool path（`approve_task` 等）を経由する設計に
- `focus` の自動更新（IDE 側フックから `heartbeat_session` を叩く）
