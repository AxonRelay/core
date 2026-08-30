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

---

## 11. 2026-06 アップデート（地形の変化と方針調整）

> 追記: 2026-06 / 本仕様（§1–10）は 2026-04-29 時点の決定ログ。以下は実地確認した業界変化と、それに伴う方針の調整。§1–10 の記述自体は履歴として保持する。

### 11.1 確定した価値の軸 — 「ガバナンス台帳」特化

AxonRelay の残存価値を **「人＋AI 混成チームの永続的な承認・改稿台帳（誰が／どの版に／何を／誰がどのコメントで承認・差戻したか）を MCP で公開する」** ことに一点集中する。オーケストレーションや HITL の一時停止では戦わない。

### 11.2 MCP `elicitation` の標準化 → HITL 一時停止はコモディティ化

MCP に `elicitation`（サーバが人間に構造化入力を accept/decline/cancel で要求）が標準化された。「人間に承認を求めて止まる」こと自体は **もはや差別化要因ではない**。
- **含意:** AxonRelay の差別化は「一時停止」ではなく「**揮発しない台帳**」にある（elicitation はセッション内・記録なし）。
- **将来オプション:** 承認モーメントを elicitation 経由でも公開し、任意の MCP クライアントがネイティブ承認 UI を得られるようにする（台帳への記録は AxonRelay 側で担保）。Phase 3 候補。

### 11.3 A2A v1.0 stable → §8 の「対象外」スタンスを撤回（候補に格上げ）

§8 で「A2A は採用が固まり切っていない（対象外）」としたが、A2A は **2026-04 に v1.0 stable**（Linux Foundation ホスト、150+ 組織、署名付き Agent Card、決済 AP2、Google/MS/AWS が GA）。
- **調整:** A2A を恒久的対象外から **「採用候補（個人 PoC では優先度低）」** に格上げ。
- **設計余地の確保:** AI Actor 間連携（A2A 由来のアクション）も将来 `Approval` / `ExternalLink` 台帳に記録できる余地を残す。実装は急がない。

### 11.4 EU AI Act full enforcement（2026-08-02）→ 台帳を value prop の中核に

高リスク AI の義務が 2026-08-02 に full enforcement。その3本柱（①改ざん耐性の行動ログ ②高影響行動の人間承認ゲート ③全行動の identity 帰属）は AxonRelay の残存核とほぼ一致する。
- **調整:** §8 で「規制グレード対応は対象外」とした方針は維持する（電子署名 / タイムスタンプ局 / 21 CFR Part 11 等は引き続き対象外）。ただし **「監査台帳」という value prop は中核に据える** — 規制準拠製品ではなく、その思想のリファレンス実装として dogfood する。

### 11.5 LangGraph Platform → LangSmith Deployment 改称 / self-host 退避路

LangGraph Platform は **LangSmith Deployment** に改称。OSS の drop-in 代替 **Aegra** が登場。
- **調整:** [step2-plan.md](./step2-plan.md) のリスク表「Platform 料金が過剰」の退避策に **Aegra（self-host drop-in）** を追加（既存の self-hosted LangGraph Server 案に加えて）。

### 11.6 既知の制約 — 承認台帳は「単一書き込み者」前提 〔2026-08 解消〕

> **更新 (2026-08-30):** 本項が Phase 3+ 候補として先送りしていた「複数アクター / リモート同時利用への拡張」は実施済み。並行承認の問題は解消した。経緯は [coordination-spec.md §9](./coordination-spec.md) を参照。以下は当時の記述を履歴として残す。

承認台帳の hash chain と承認フローは **単一オペレータが直列に承認する前提**で設計している（§2 の self = 人間1人固定に整合）。以下は **個人 PoC では発生しない**として意図的に対象外:

- **同一 task への並行承認**: `record_approval` は「直前行読取 → prev_hash 計算 → INSERT」をロックなしで行うため、同一 task を**同時に**承認/差戻しすると hash chain が分岐し `verify_approval_chain` が後発行を誤検知し得る。複数書き込み者を導入する際は承認追記を task 単位で直列化（行ロック / 楽観ロック）する必要がある。
  → **解消済み**: `record_approval` は chain 先端を読む前に task 行のロックを取る（Postgres では `SELECT ... FOR UPDATE`）。書き込みは task 単位で直列化される。
- **`review_pending_task` の TOCTOU**: elicitation 応答待ちの間に task が変化した場合、表示時の draft と一致しなければ `stale_decision` を返して**記録しない**ガードを実装済み（見ていない内容を承認しない）。最終 append との極小窓のみ単一ユーザ前提で許容。
  → ガードは有効のまま。複数書き込み者の下ではより重要になる。

---

## 12. 2026-08 アップデート — Phase 3 調整レイヤー

§2 は Actor を「self + 2〜3 AI」で足りるとしていた。実際の dogfood はその想定を超えた: **複数デバイス・複数リポジトリ・同一リポジトリの複数 clone で、Claude と Codex が並行して**動いている。台帳が答えない問い（いま誰がどこで何を触っているか、これから編集する場所は空いているか、別 clone に前提をどう伝えるか）が恒常的に発生する。

これを AxonRelay の機能として引き取ったのが **Phase 3 調整レイヤー**である。Workspace / Session / Claim / Relay の4概念を追加し、MCP から駆動する。設計と運用プロトコルは [coordination-spec.md](./coordination-spec.md)。

方針の位置づけ:

- **ピボットの核（ガバナンス台帳）は変えない。** 調整レイヤーは台帳の前段であり、置き換えではない。「誰が承認したか」に「誰がいま何を握っているか」が加わる
- **自前ランタイムを取り戻すものではない。** 実行は LangGraph Platform のまま。調整レイヤーはメッセージブローカーを持たず、Postgres 1テーブルと pull 配信で足りる（[ADR-006](./adr-006-no-message-broker.md)）
- **§8 の out of scope は維持。** マルチテナント / 複数ユーザー / 勤務先データは引き続き対象外。増えたのは「1人のオペレータが動かす複数のエージェントとデバイス」であって、他人ではない
