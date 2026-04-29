# δ MVP 仕様 — Step 1 成果物

> 作成: 2026-04-29 / Status: ドラフト（合意取得待ち）

---

## 1. 目的とスコープ

AxonRelay を「個人タスク管理 — 複数 AI エージェント + 自分の承認」のドッグフードプラットフォームに作り替える。本人（エンジニア）が日常で発生する小〜中サイズのタスク（設計検討・調査・分解・レビュー）を、複数の AI Actor が executor / reviewer として動き、本人が approver として最終承認する形で実行・記録する。

**メイン UI は IDE（Claude Code / Cursor / Zed）** で、AxonRelay 自身が **MCP サーバとして公開**される。承認は IDE のチャットで完結。Web ダッシュボードは履歴閲覧用の最小 AG-UI 実装に限定。

**残す核:** Actor 統一抽象 / Assignment ロール / Draft 履歴 / Approval 台帳 / ExternalLink。
**捨てる:** ランタイム自前実装、自前 HITL UI、自前認証、マルチテナント、AWS EC2 本番運用、Caddy。
**任せる先:**
- LangGraph Platform（agent runtime / checkpoint / observability）
- **MCP（AxonRelay 自身を MCP サーバ化、外部リソース接続も MCP）**
- AG-UI + CopilotKit（履歴閲覧の薄い Web ダッシュボードのみ）
- Cloudflare Tunnel + Tailscale（ホスティング / 個人デバイス間メッシュ）
- Vercel / Cloudflare Pages（薄いダッシュボードホスティング）

---

## 2. Actor モデル（最小構成）

| Actor 種別 | 例 | 想定ロール |
|---|---|---|
| Human (self) | 本人ひとり固定 | approver、場合により executor |
| AI: writer | LLM ドラフト生成 | executor |
| AI: reviewer | LLM レビュー | reviewer |
| AI: validator | linter / 静的チェック LLM | reviewer |
| AI: researcher | 公開 web / OSS docs 検索 | executor |

個人 PoC では Actor は **self + 2〜3 AI** で足りる。`User` / `Project` / `ProjectMember` の概念は **削除**し、Actor.id=self_human の単一固定とする。

---

## 3. ステート遷移

```
DRAFT ──► WAITING_REVIEW ──► WAITING_APPROVAL ──► APPROVED ──► COMPLETED
              │                       │
              └─► NEEDS_REVISION ◄────┘
                       │
                       └─► DRAFT
                       └─► CANCELLED
```

**新規追加:** `WAITING_REVIEW`（AI executor 完了 → AI reviewer 実行中/完了）、`NEEDS_REVISION`（差戻し）。
**流用:** 既存の `DRAFT` / `WAITING_APPROVAL` / `APPROVED` / `REJECTED` / `COMPLETED` / `CANCELLED`。

---

## 4. インターフェース表面

### 4.1 MCP サーバ（メインインターフェース）

AxonRelay は **MCP サーバ** として `axonrelay-mcp` を公開する。IDE（Claude Code / Cursor / Zed）から直接ツール呼び出しできる。

| MCP Tool | 説明 |
|---|---|
| `list_tasks` | 自分のタスク一覧（status filter 可） |
| `create_task` | タスク作成（title / description / assigned_agents） |
| `get_task` | タスク詳細（assignments / drafts / approvals 同梱） |
| `run_task` | LangGraph Platform に実行委譲（thread_id 紐付け） |
| `list_pending_approvals` | 自分が approver で WAITING_APPROVAL のタスク |
| `approve_task` | 承認（comment / 任意の modified_draft） |
| `reject_task` | 差戻し（comment / reason） |
| `get_drafts` | ドラフト履歴 |
| `list_agents` | AI Actor 定義一覧 |
| `create_agent` / `update_agent` | AI Actor 定義の CRUD |

| MCP Resource | URI | 説明 |
|---|---|---|
| Task | `axonrelay://tasks/{id}` | タスク詳細を resource として読める |
| Draft | `axonrelay://tasks/{id}/drafts/{version}` | 特定バージョンのドラフト |

### 4.2 HTTP API（薄いダッシュボード用）

ダッシュボードと内部から最小限。MCP と同じ機能を REST で提供（重複実装ではなく内部 service を共通化）。

| Method | Path | 説明 |
|---|---|---|
| GET | /api/tasks | タスク一覧 |
| GET | /api/tasks/{id} | タスク詳細 |
| GET | /api/tasks/{id}/drafts | ドラフト履歴 |
| GET | /api/agents | AI Actor 定義一覧 |
| GET | /api/events (SSE) | AG-UI 互換イベントストリーム |

書き込み系（create/approve/reject）は MCP 側を一次経路とし、HTTP は読み取り中心。

### 4.3 削除

- `/projects/*` 全削除（Project / ProjectMember ごと）
- `/auth/sync` + NextAuth + Google OAuth（単一ユーザー化）
- `/users/*` 全削除
- `/task/start` / `/task/{thread_id}` / `/task/approve` の旧フォーマット（新 MCP / API に統合）
- 旧 Next.js の承認 UI 系コンポーネント全削除

---

## 5. データモデル

### 残す

- `Actor` (type=human/ai)
- `AgentDefinition` (AI Actor の config)
- `Task`（`project_id` を **削除**して独立化）
- `TaskAssignment` (executor / reviewer / approver / observer)
- `Draft`（バージョン履歴）
- `Approval`（action / comment / reviewer_actor_id）
- `ExternalLink`（type / url / metadata。MCP リソース URI を将来格納）

### 削除

- `User`（Actor 単一固定で代替）
- `Project` / `ProjectMember`（マルチプロジェクト不要）

### 変更

- `Task.project_id` → 削除
- `Task.creator_id` → `creator_actor_id`（Actor 参照に統一）
- `Approval.reviewer_id` → `reviewer_actor_id`（Actor 参照に統一）

---

## 6. 削除するコード・インフラ

| 対象 | 理由 |
|---|---|
| `backend/app/graph.py` の自前 LangGraph グラフ | Platform にデプロイ。Backend は HTTP クライアントだけ持つ |
| Redis (checkpoint 用途) | Platform 側に寄せる |
| `Caddyfile` + 本番 docker-compose 構成 | Cloudflare Tunnel に置き換え |
| `infra/`（AWS EC2 / DNS / SSL） | EC2 は廃止。DNS は Cloudflare に移管 |
| `frontend/` の **承認 UI / タスク投入 UI 全部** | MCP に置き換え（IDE で完結） |
| `frontend/` のうちダッシュボード相当 | 履歴閲覧用の最小 AG-UI 実装に作り直し（薄く） |
| NextAuth + Google OAuth | 単一ユーザー化により不要 |
| User / Project 系の alembic マイグレーション | drop 用のマイグレーションを 1 本追加 |
| AWS EC2 インスタンス | 停止・解約 |

---

## 7. 外部依存（取り込む標準）

| カテゴリ | 採用 | 役割 |
|---|---|---|
| Agent runtime | **LangGraph Platform** | グラフ実行 / checkpoint / observability / time-travel |
| Agent ↔ IDE | **MCP（AxonRelay 自身を MCP サーバ化）** | メインインターフェース。IDE から承認操作 |
| Agent ↔ 外部リソース | **MCP クライアント** | OSS docs / 公開 web / 自分の Obsidian・Drive を接続（社内データは恒久的に対象外） |
| Agent ↔ ダッシュボード | **AG-UI + CopilotKit** | 履歴閲覧の薄い Web UI のみ |
| ホスティング (backend) | **Cloudflare Tunnel** | 自宅 PC や VPS から `api.axonrelay.com` を公開 IP なしで提供 |
| ホスティング (dashboard) | **Vercel / Cloudflare Pages 無料枠** | `app.axonrelay.com` で薄い AG-UI ダッシュボード |
| 個人デバイス間メッシュ | **Tailscale** | IDE から AxonRelay MCP サーバへの private 接続経路 |
| DNS | **Cloudflare DNS（既存 axonrelay.com を維持）** | サブドメイン分割 (`api`, `app`, `mcp` 等) |
| 廃止 | AWS EC2 / Redis / Caddy / NextAuth / OAuth | — |

---

## 8. Out of scope（明示的に対象外）

- 規制グレード対応（電子署名 / タイムスタンプ局 / 改ざん検知ハッシュ / 21 CFR Part 11 / GxP）
- マルチテナント / マルチプロジェクト / 複数ユーザー
- **勤務先業務データの取り扱い（恒久的に対象外）**
- A2A プロトコル（採用が固まり切っていない）
- 自前メモリ / 自前 RAG / 自前観測ダッシュボード
- ε（ドキュメント横断検索 + 要約）— Phase 2 へ繰り延べ、source は公開・個人データに限定して再設計

---

## 9. dogfood シナリオ（具体）

1. **設計判断ログ**
   投入: 「X と Y のどちらを採用するか」
   フロー: researcher AI が比較ドラフト → reviewer AI が観点抜け指摘 → 自分が approver として判断 + コメント
   残るもの: Draft 全版 + Approval 台帳

2. **タスク分解**
   投入: 「この issue を実装計画に分解して」
   フロー: writer AI が分解ドラフト → reviewer AI が抜け漏れ・依存関係チェック → 自分が承認 or `NEEDS_REVISION` で再ドラフト

3. **公開リサーチ要約**
   投入: OSS docs / 公開記事の URL
   フロー: researcher AI が要約 + 引用 → reviewer AI が事実誤認チェック → 自分が承認 / 差戻し
   制約: source は **公開データのみ**（社内データを source にしない）

---

## 10. Step 2 への引き継ぎ事項

- LangGraph Platform のアカウント・プロジェクト準備
- Platform 側にデプロイする graph 定義（writer / reviewer / approver の 3 ノード以上）の設計
- Backend を Platform クライアントに置き換える境界線の確定
- 削除するファイル一覧の確定（infra/, Caddyfile, NextAuth 系, User/Project 系の models / crud / schema / endpoint, frontend の承認 UI 系）
- マイグレーション戦略（個人 PoC なので drop & recreate 可）
- **MCP サーバ実装**（`axonrelay-mcp`、tools / resources の整備）
- **Cloudflare Tunnel + Tailscale + Vercel/Pages** へのホスティング切替
- **AWS EC2 解約・本番ドメイン切替** の段取り（DNS 切替時のダウンタイム計画）
