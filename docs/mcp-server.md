# AxonRelay MCP Server

AxonRelay は **MCP サーバ** として動作し、Claude Code / Cursor / Zed などの IDE から直接タスク作成・承認・差戻しができる。

## 起動

### stdio transport（IDE から直接起動 — 推奨）

```bash
cd backend
python -m app.mcp.server
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

## 提供する Resources

| URI | 内容 |
|---|---|
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

## Phase 2.4 以降の拡張

- Streamable HTTP transport を FastAPI にマウント（`/mcp` エンドポイント）→ Cloudflare Tunnel 経由でリモート MCP クライアントから接続可能に
- Discord / モバイル経由の承認も同じ tool path（`approve_task` 等）を経由する設計に
