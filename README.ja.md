# AxonRelay / core

日本語 | **[English](README.md)**

**人＋AI 混成チームのためのガバナンス台帳を、MCP サーバとして公開する。**

AxonRelay は「**誰が**（人か AI か）、**どのドラフト版**に対して、**何をしたか**、そして
**誰が・どんなコメントで・いつ承認/差戻したか**」を記録する。エージェントランタイム、
人間の介入（承認待ち）、UI はすべて標準（LangGraph Platform / MCP / AG-UI）に委譲する。
AxonRelay が自分で持ち続けるのは、それらの標準が**提供しない**部分——
**永続的で、セッションをまたぎ、Actor をまたぐ承認・改稿台帳**だけ。

> **状態: 個人 PoC（ピボット後）。** 元は汎用「AI Agent Orchestration」基盤だったが、
> その層は LangGraph Platform + MCP + AG-UI でコモディティ化したため、唯一持つ価値の
> ある「ガバナンス台帳」に絞り込んだ。backend（Actor モデル / 台帳 / MCP サーバ /
> Platform クライアント）は移行済み。旧 Next.js の auth/projects UI と AWS インフラは
> 撤去途中——[現状](#現状) を参照。

---

## なぜ存在するのか（2026 年中盤の文脈）

2026 年中盤までに agentic スタックは 3 層に定着した——ツールは **MCP**、エージェント間は
**A2A**、エージェント↔UI は **AG-UI**。そして「人間に確認を求めて一時停止する」ことは
MCP のネイティブ機能（`elicitation`）になった。**承認のための一時停止は、もはや差別化要因ではない。**

これらの層が与えてくれないのは「**説明責任の永続的な記録**」だ。elicitation は揮発的で
セッション内限り、observability ツールは run を記録するが「誰が何を承認したか」は記録しない。
一方で EU AI Act の高リスク義務は **2026-08-02 に full enforcement** を迎え、その中核要求は
まさに——改ざん耐性のある行動ログ、高影響行動への人間承認ゲート、そしてすべての行動を
責任ある identity（人**または**エージェント）に帰属させること——である。

AxonRelay は人間をこの台帳の **第一級 Actor** として扱う（interrupt 境界の外側の例外としてではなく）。
これがこのリポジトリが保持し、dogfood する残存価値だ。

---

## アーキテクチャ

```
IDE (Claude Code / Cursor / Zed)
   │  MCP（ローカルは stdio、将来は Tunnel 経由の Streamable HTTP）
   ▼
AxonRelay Backend（FastAPI + MCP サーバ同居）
   │   - MCP : 14 tools + 2 resources（メインインターフェース）
   │   - REST: /tasks /agents /actors（読み取り中心・ダッシュボード用）
   │   - Postgres: Platform thread state の射影 → 台帳
   │
   └──▶ LangGraph Platform（runtime / checkpoint / observability）
            └──▶ writer → reviewer → [interrupt: human_approval] → finalize
                     └──▶ LLM (Claude / GPT)
```

| 構成要素 | 役割 |
|---------|------|
| **MCP サーバ** | メインインターフェース。IDE からタスク作成・承認・差戻し。backend と同居（[`backend/app/mcp/`](backend/app/mcp/)）。 |
| **Backend (FastAPI)** | 薄い REST 層 ＋ 台帳を所有する SQLAlchemy service 層。 |
| **PostgreSQL** | 台帳本体: Actor / TaskAssignment / Draft（版付き）/ Approval / ExternalLink。Platform thread state の射影。 |
| **LangGraph Platform** | エージェントランタイム・checkpoint・observability。graph は [`axonrelay-graph/`](axonrelay-graph/)。（現在は *LangSmith Deployment* に改称。Aegra 等で self-host も可。） |

backend は **読み取り中心**の設計: 書き込み（作成・承認・差戻し）は MCP 経由が一次経路で、
REST API は主にダッシュボードから台帳を読むためにある。

---

## データモデル — 台帳

| モデル | 役割 |
|-------|------|
| `Actor` | 人と AI を同型で扱う統一抽象。人間 Actor（`name="self"`）1 件がオペレータ。AI Actor は `AgentDefinition` と 1:1。 |
| `TaskAssignment` | Actor を Task にロール付きで紐付け: `executor` / `reviewer` / `approver` / `observer`。 |
| `Draft` | タスク出力の版付き履歴。 |
| `Approval` | 追記専用の記録: `action`（approved/rejected）/ `comment` / `reviewer_actor_id` / timestamp。per-task SHA-256 hash chain（`prev_hash` / `entry_hash`・[`app/ledger.py`](backend/app/ledger.py)）で改ざん検出可能。 |
| `ExternalLink` | 外部成果物へのリンク（将来の MCP リソース URI など）。 |

ステートマシン:

```
DRAFT → WAITING_REVIEW → WAITING_APPROVAL → APPROVED → COMPLETED
            │                    │
            └─► NEEDS_REVISION ◄─┘ ─► DRAFT / CANCELLED
```

---

## インターフェース

### MCP サーバ（一次）

14 tools（`list_tasks`, `create_task`, `get_task`, `run_task`,
`list_pending_approvals`, `approve_task`, `reject_task`, `review_pending_task`
（MCP elicitation による対話的承認）, `verify_task_ledger`, `get_drafts`,
`list_agents`, `create_agent`, `update_agent`, `get_self_actor`）と 2 resources
（`axonrelay://tasks/{id}`, `axonrelay://tasks/{id}/drafts/{version}`）。

tool リファレンスと Claude Code 設定: **[docs/mcp-server.md](docs/mcp-server.md)**。

### REST API（読み取り中心・ダッシュボード用）

| Method | Path | 説明 |
|--------|------|------|
| `GET` | `/` | ヘルスチェック |
| `GET` | `/actors` `/actors/{id}` `/actors/me` | Actor |
| `GET`/`POST`/`PUT`/`DELETE` | `/agents` `/agents/{id}` | AI エージェント定義 |
| `GET`/`POST`/`PUT`/`DELETE` | `/tasks` `/tasks/{id}` | タスク |
| `POST` | `/tasks/{id}/run` | Platform で interrupt/完了まで実行 |
| `GET` | `/tasks/pending/approvals` | 統一承認受信箱 |
| `POST` | `/tasks/{id}/approve` `/tasks/{id}/reject` | 承認 / 差戻し |
| `GET` | `/tasks/{id}/drafts` | ドラフト履歴 |
| `GET` | `/tasks/{id}/ledger/verify` | 承認 hash chain の改ざん検証 |
| `GET`/`POST`/`DELETE` | `/tasks/{id}/assignments` | タスク割り当て |

書き込み系は MCP 側からも公開され、IDE からはそちらが一次経路。

---

## Quick Start

```bash
cp .env.example .env          # LANGGRAPH_* と ANTHROPIC_API_KEY を記入
docker compose up -d postgres # Postgres のみ。runtime は Platform 側

# backend（venv 推奨）
pip install -r backend/requirements.txt
cd backend && alembic upgrade head   # migration 003 適用・"self" Actor を seed

# IDE 用に MCP サーバ（stdio）を起動
python -m app.mcp.server
```

その後 Claude Code を接続する — [docs/mcp-server.md](docs/mcp-server.md) を参照。

Platform にデプロイする前にローカルで graph を動かす:

```bash
cd axonrelay-graph && pip install -e . && langgraph dev
```

---

## 環境変数

| 変数 | デフォルト | 説明 |
|------|-----------|------|
| `DATABASE_URL` | `postgresql://axonrelay:axonrelay_dev@postgres:5432/axonrelay` | Postgres 接続文字列 |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | `axonrelay` / `axonrelay` / `axonrelay_dev` | Postgres 初期化（ローカル以外ではパスワード変更） |
| `LANGGRAPH_API_URL` | — | LangGraph Platform エンドポイント（タスク実行に必須） |
| `LANGGRAPH_API_KEY` | — | Platform API キー |
| `LANGGRAPH_ASSISTANT_ID` | `axonrelay` | デプロイ済み graph / assistant id |
| `LLM_PROVIDER` | `anthropic` | `anthropic` または `openai`（graph が使用） |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | — | LLM 認証情報 |
| `WRITER_MODEL` / `REVIEWER_MODEL` | `claude-sonnet-4-6` / `claude-haiku-4-5-20251001` | ロール別モデル |
| `DISCORD_*` | — | Discord モバイル承認（Phase 2.6・未接続） |

データベースのセットアップとマイグレーションは [SETUP_POSTGRES.md](SETUP_POSTGRES.md) を参照。

---

## 現状

ピボットは **一部完了** — backend は完了、frontend/インフラの掃除が未着手:

- ✅ **Backend**: Actor ベースの台帳、MCP サーバ（14 tools / 2 resources）、LangGraph Platform クライアント、migration 005 まで。承認台帳は改ざん耐性あり（per-task SHA-256 hash chain、`verify_task_ledger` で検証）。台帳は単一書き込み者（オペレータ）前提で、同一 task への並行承認は PoC では対象外（[delta-mvp-spec §11.6](docs/delta-mvp-spec.md) 参照）。
- ✅ **Graph**: `axonrelay-graph/`（writer → reviewer → human_approval → finalize）が Platform 用に準備済み。
- ✅ **Frontend**: 薄い**読み取り専用**ダッシュボード（Vite + React + TS・[`frontend/`](frontend/)）。タスク一覧（status filter）/ ドラフト履歴 / 承認 timeline / task ごとの台帳検証バッジ。書き込みは MCP/IDE 経路のまま。（CopilotKit/AG-UI は読み取り専用には不要なため見送り。）
- 🚧 **Infra**: `infra/`（AWS DNS）と旧 `Caddyfile` / 本番構成は撤去予定（Cloudflare Tunnel + Tailscale へ切替、Phase 2.4）。
- 🚧 **Tests**: 台帳の不変条件と run-state 投影の idempotency を pytest で整備（`backend/tests/`・CI の py3.12 で実行）。より広いカバレッジは今後。

ロードマップと移行計画: [docs/step2-plan.md](docs/step2-plan.md)。ピボットの背景とスコープ: [docs/delta-mvp-spec.md](docs/delta-mvp-spec.md)。

---

## ドキュメント

- [docs/delta-mvp-spec.md](docs/delta-mvp-spec.md) — ピボット仕様（Actor モデル / スコープ / dogfood シナリオ）
- [docs/step2-plan.md](docs/step2-plan.md) — 移行計画（Phase 2.1–2.6）
- [docs/mcp-server.md](docs/mcp-server.md) — MCP サーバ接続ガイド & tool リファレンス
- [docs/discord-setup-guide.md](docs/discord-setup-guide.md) — Discord モバイル承認セットアップ
- [SETUP_POSTGRES.md](SETUP_POSTGRES.md) — PostgreSQL セットアップ & マイグレーション
