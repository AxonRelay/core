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

調整レイヤーは **全員が同じインスタンスを見ていること**が前提なので、デバイスが複数あるならこちらを使う。既定のバインドは loopback。**呼び出し元認証はない**ので、他デバイスからの到達は Tailscale か Cloudflare Tunnel 経由にする（[deploy/DEPLOYMENT.md](../deploy/DEPLOYMENT.md)）。公開バインドは想定外。

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

`~/.claude/settings.json`（または プロジェクト `.claude/settings.json`）に追記：

```json
{
  "mcpServers": {
    "axonrelay": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "/Users/dev/workspace/AxonRelay/core/backend",
      "env": {
        "DATABASE_URL": "postgresql://axonrelay:axonrelay_dev@localhost:5432/axonrelay",
        "LANGGRAPH_API_URL": "https://your-langgraph-platform-url",
        "LANGGRAPH_API_KEY": "lsv2_...",
        "LANGGRAPH_ASSISTANT_ID": "axonrelay"
      }
    }
  }
}
```

> **注意**: `cwd` は backend/ の絶対パス。Postgres / LangGraph Platform の接続情報が必要。

## 提供する Tools

| Tool | 用途 |
|---|---|
| `list_tasks(status?, limit?)` | タスク一覧 |
| `list_pending_approvals()` | **統一承認受信箱** — 最も重要 |
| `get_task(task_id)` | タスク詳細（assignments / drafts / approvals 同梱） |
| `get_drafts(task_id)` | ドラフト履歴 |
| `create_task(title, description?, assignments?)` | 新規タスク + Platform thread 確保 |
| `run_task(task_id)` | Platform 上で graph 実行 → 承認待ちまで |
| `approve_task(task_id, comment?, modified_draft?)` | 承認（任意で edit） |
| `reject_task(task_id, comment?, reason?)` | 差戻し（revision loop） |
| `review_pending_task(task_id)` | **対話的承認** — MCP `elicitation` でドラフトを提示し承認/差戻しを尋ね、台帳記録 + resume まで一括。elicitation 非対応クライアントは `approve_task`/`reject_task` を使う |
| `verify_task_ledger(task_id)` | 承認 hash chain の改ざん検証（`{valid, broken_at, count, legacy}`。`legacy` は hash chain 導入前の行数） |
| `list_agents(agent_type?, is_active?)` | AI Actor 一覧 |
| `create_agent(name, agent_type, ...)` | AI Actor 定義 |
| `update_agent(agent_id, ...)` | AI Actor 更新 |
| `get_self_actor()` | オペレータ Human Actor |

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
