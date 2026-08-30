# Step 2 Plan — 自前ランタイム/UI/Auth/インフラの解体と LangGraph Platform + MCP 移行

> 作成: 2026-04-29 / 前提: [delta-mvp-spec.md](./delta-mvp-spec.md) に基づく / Status: ドラフト（合意取得待ち）

---

## ゴール

[delta-mvp-spec.md](./delta-mvp-spec.md) の構成に到達する：

- **AxonRelay backend = MCP サーバ + 薄い HTTP API + Postgres**
- **Agent runtime = LangGraph Platform**
- **UI = IDE (MCP 経由) + 最小 AG-UI ダッシュボード**
- **Hosting = Cloudflare Tunnel + Tailscale + Vercel/Pages**
- **EC2 / Caddy / Redis / NextAuth / User / Project / 自前 graph.py = 全削除**

## 進め方の原則

1. **本番 (`https://axonrelay.com`) は Step 2 完了まで動かしたまま**にして、Cloudflare 切替で原子的にカットオーバーする（DNS TTL を事前に短縮）
2. **drop & recreate** で良い（個人 PoC、データに価値なし）
3. 1 PR / 1 マイルストーン単位で進め、各段階で動作確認できる粒度に分ける
4. リスクの高い切替（Platform 移行 / DNS 切替）は最後にまとめる

---

## Phase 2.1: スキーマとコードの掃除（破壊的変更を一気に）

**目的:** ガバナンス層だけ残す。User / Project / 旧 graph / 認証を削除。

| # | タスク | 対象ファイル |
|---|---|---|
| 2.1.1 | `User` / `Project` / `ProjectMember` テーブル drop マイグレーション | `backend/alembic/versions/` に新規 |
| 2.1.2 | `Task.project_id` 削除、`creator_id` → `creator_actor_id` リネーム | `models.py` / マイグレーション |
| 2.1.3 | `Approval.reviewer_id` → `reviewer_actor_id` リネーム | 同上 |
| 2.1.4 | `User` / `Project` / `ProjectMember` モデル削除 | `models.py` |
| 2.1.5 | `crud.py` から user / project 系関数を削除 | `crud.py` |
| 2.1.6 | `schema.py` から user / project 系の Pydantic モデルを削除 | `schema.py` |
| 2.1.7 | `main.py` から `/auth/sync` / `/projects/*` / `/users/*` エンドポイント全削除 | `main.py` |
| 2.1.8 | 旧 `/task/start` `/task/{thread_id}` `/task/approve` を新形式に置換 | `main.py` |
| 2.1.9 | `init` 時に `Actor(type=human, name="self")` を 1 件 seed する仕掛け | `database.py` or 起動スクリプト |
| 2.1.10 | New ステート (`WAITING_REVIEW`, `NEEDS_REVISION`) の Enum 追加 | `models.py` |
| 2.1.11 | NextAuth + Google OAuth + frontend の auth 関連コード削除 | `frontend/src/auth.ts`, 関連 page |
| 2.1.12 | `SETUP_GOOGLE_OAUTH.md` 削除 | ルート |
| 2.1.13 | `requirements.txt` から不要依存（slowapi 等）を見直し | `backend/requirements.txt` |

**完了条件:** `docker compose up` でローカルが起動し、新 schema でタスク CRUD ができる（runtime はまだ自前 LangGraph 経由でも可）。

---

## Phase 2.2: LangGraph Platform 移行

**目的:** agent runtime を Platform に委譲。`backend/app/graph.py` を削除し、Backend は Platform クライアントだけに痩せる。

| # | タスク |
|---|---|
| 2.2.1 | LangGraph Platform アカウント作成・personal プロジェクト準備（個人アカウントで） |
| 2.2.2 | Platform 側にデプロイする graph を別ディレクトリに新規作成（仮: `axonrelay-graph/`）— writer / reviewer / approver(interrupt) の 3 ノード構成 |
| 2.2.3 | Graph の State スキーマ確定（task_id, drafts[], reviewer_comments[], status） |
| 2.2.4 | LLM 呼び出しを実装（OpenAI or Anthropic、API キーは Platform secrets） |
| 2.2.5 | Platform にデプロイ、SDK 経由で thread を作成・実行できることを CLI で確認 |
| 2.2.6 | Backend 側に `langgraph_client.py` 追加（Platform SDK ラッパ） |
| 2.2.7 | `POST /api/tasks/{id}/run` エンドポイント実装 — Platform に thread 作成 → 実行 → state を Postgres の Draft / Approval に同期 |
| 2.2.8 | Platform の interrupt（承認待ち）→ Backend `/approve` → Platform に resume の往復を実装 |
| 2.2.9 | `backend/app/graph.py` および Redis 関連の依存を削除 |
| 2.2.10 | `docker-compose.yml` から Redis サービス削除 |

**完了条件:** タスク作成 → run → Platform 上で writer ノード実行 → reviewer ノード → 承認待ち → Backend から approve → 完了、までが通る。

---

## Phase 2.3: MCP サーバ化（メインインターフェース）

**目的:** AxonRelay を IDE から直接触れる MCP サーバとして公開する。

**統合クライアントの優先順位:**
1. **第一優先: Claude Code** — 完了条件はこれが動くこと
2. 第二優先: Codex（OpenAI Codex CLI） — **当面後回し**
3. それ以降: Cursor / Zed / n8n / GitHub Action（必要が出たら追加）

| # | タスク |
|---|---|
| 2.3.1 | MCP server 実装の言語・SDK 選定（Python `mcp` SDK で Backend と同居推奨） |
| 2.3.2 | `axonrelay-mcp` パッケージのスケルトン作成 |
| 2.3.3 | Tools 実装: `list_tasks`, `create_task`, `get_task`, `run_task`, `list_pending_approvals`, `approve_task`, `reject_task`, `get_drafts`, `list_agents`, `create_agent`, `update_agent` |
| 2.3.4 | Resources 実装: `axonrelay://tasks/{id}`, `axonrelay://tasks/{id}/drafts/{version}` |
| 2.3.5 | Tools / Resources の入出力スキーマを Pydantic で型付け |
| 2.3.6 | MCP サーバから Backend HTTP API を叩く構成（or 同居なら直接 service 層を呼ぶ） |
| 2.3.7 | **Claude Code** に MCP 設定を入れて手動 dogfood 確認（`~/.claude/settings.json` の `mcpServers` に `axonrelay` を追加） |
| 2.3.8 | MCP サーバの README（接続方法、tools 一覧）を docs に追加 |
| 2.3.9 | Codex 対応は別 issue として記録（Phase 3 候補） |

**完了条件:** Claude Code から「`approve_task` で承認」「`create_task` で投入」「`list_pending_approvals` で受信箱を取得」が IDE のチャットから動く。

---

## Phase 2.4: ホスティング切替（カットオーバー）

**目的:** AWS EC2 を捨て、Cloudflare Tunnel + Tailscale + Vercel/Pages に乗せ換える。`axonrelay.com` は維持。

| # | タスク |
|---|---|
| 2.4.1 | Cloudflare アカウントに `axonrelay.com` の DNS を移管（既に Cloudflare なら skip） |
| 2.4.2 | Tailscale 個人ネットワークを準備、PoC を回すデバイスを join |
| 2.4.3 | 自宅 PC or 小規模 VPS に Backend (Postgres + FastAPI + MCP) を docker compose で起動 |
| 2.4.4 | Cloudflare Tunnel をインストール、`api.axonrelay.com` → 自宅 PC の Backend にルート |
| 2.4.5 | 薄い AG-UI ダッシュボードのスケルトン（Next.js or Vite + CopilotKit）を Phase 2.5 用に新規作成し、Vercel か Cloudflare Pages にデプロイ → `app.axonrelay.com` |
| 2.4.6 | DNS TTL を事前に短縮（300s 以下） |
| 2.4.7 | `https://axonrelay.com` を `app.axonrelay.com` にリダイレクト or トップに変更 |
| 2.4.8 | E2E 動作確認後、AWS EC2 インスタンスを停止 → スナップショット → 削除 |
| 2.4.9 | `infra/` ディレクトリ削除、`Caddyfile` 削除、本番 `docker-compose.yml` の本番設定削除 |

**完了条件:** EC2 が消え、`axonrelay.com` 配下が新構成で稼働、月額固定費がほぼゼロ（Tailscale Free + Cloudflare Free + Vercel Free）。

---

## Phase 2.5: 最小 AG-UI ダッシュボード

**目的:** タスク履歴・ドラフト履歴・Approval 台帳をブラウザで俯瞰できる薄い UI。承認操作は MCP 側に任せる。

| # | タスク |
|---|---|
| 2.5.1 | CopilotKit + AG-UI のサンプルを起点にスケルトン作成 |
| 2.5.2 | タスク一覧ページ（status filter / date filter） |
| 2.5.3 | タスク詳細ページ（assignments / drafts diff / approvals timeline） |
| 2.5.4 | AI Actor 定義の閲覧ページ（編集は MCP に任せる） |
| 2.5.5 | SSE で `/api/events` を購読してリアルタイム更新 |
| 2.5.6 | 認証（個人 PoC なので Cloudflare Access の email allowlist で 1 アカウント許可、または basic auth） |

**完了条件:** `app.axonrelay.com` で履歴を俯瞰できる。承認はしない（IDE/MCP 側で完結）。

---

## Phase 2.6: Discord Notification + Mobile Approval

**目的:** AxonRelay の承認操作をスマホ Discord で完結させる。複数 AI ソース（Claude Code、自前 LangGraph、将来の Codex 等）をチャンネル別に集約。

**前提:**
- 本人の **個人** Discord アカウントを使用（勤務先業務とは無関係、constraint #3 を遵守）
- Phase 2.2（Platform）と Phase 2.4（Cloudflare Tunnel）が両方完了していること（webhook 受信 URL が必要）

### 2.6.A: Discord ゼロベース構築（手順サポートあり）

| # | タスク | 詳細・参照 |
|---|---|---|
| 2.6.A.1 | 個人 Discord アカウント準備 | 既存があれば skip。なければ https://discord.com で作成 |
| 2.6.A.2 | personal Discord サーバを新規作成 | サーバ名: `AxonRelay`（または好みの名前）。テンプレートは「友達」最小構成 |
| 2.6.A.3 | チャンネル作成 | `#pending` / `#completed` / `#source-claude-code` / `#source-langgraph` / `#audit`（最小は `#pending` + `#completed` の 2 つから始めて段階的に増やしても可） |
| 2.6.A.4 | Discord Developer Portal で Application 作成 | https://discord.com/developers/applications → New Application（名前: `AxonRelay`） |
| 2.6.A.5 | Application に Bot を追加 | Bot タブで Reset Token → **Bot Token を控える**（後で `DISCORD_BOT_TOKEN`） |
| 2.6.A.6 | 必要 Intents を有効化 | Bot タブで `MESSAGE CONTENT INTENT` を ON（後でメッセージ操作する場合） |
| 2.6.A.7 | OAuth2 URL Generator で Bot 招待 URL 生成 | scopes: `bot` `applications.commands` / permissions: Send Messages, Embed Links, Use Slash Commands |
| 2.6.A.8 | Bot をサーバに招待 | 生成 URL をブラウザで開く → 自分のサーバを選択 → 認可 |
| 2.6.A.9 | 各チャンネルの Webhook URL を取得 | チャンネル設定（歯車）→ 連携サービス → ウェブフック → 新規ウェブフック → URL コピー |
| 2.6.A.10 | Application ID と Public Key を控える | General Information タブから取得（Interactions エンドポイント検証で使用） |
| 2.6.A.11 | Interactions Endpoint URL を Application に設定 | `https://api.axonrelay.com/webhooks/discord/interactions`（Phase 2.6.B 完了後に設定） |
| 2.6.A.12 | スマホに Discord アプリ install + 通知設定 | サーバ通知を ON（@everyone でなくとも全件通知に） |

→ この A セクションは私が画面遷移レベルで一緒にガイドします（公式ドキュメントの最新 URL は実施時にアシスタントが取りに行く方針）。

### 2.6.B: Backend 実装

| # | タスク |
|---|---|
| 2.6.B.1 | `backend/app/integrations/discord/` ディレクトリ作成 |
| 2.6.B.2 | 環境変数追加: `DISCORD_BOT_TOKEN`, `DISCORD_APPLICATION_ID`, `DISCORD_PUBLIC_KEY`, `DISCORD_WEBHOOK_PENDING`, `DISCORD_WEBHOOK_COMPLETED`（チャンネル別に必要数だけ） |
| 2.6.B.3 | 通知ディスパッチャ実装：Task のステート変化（→ `WAITING_REVIEW` / `WAITING_APPROVAL` / `COMPLETED` / `NEEDS_REVISION`）を pub/sub で受け、対応 webhook に投稿 |
| 2.6.B.4 | Embed 生成: タスク title / source agent (`Actor.name`) / draft 先頭 500 字 / 詳細リンク（`app.axonrelay.com/tasks/{id}`） |
| 2.6.B.5 | Components 生成: `[✅ Approve]` `[✏️ Modify]` `[❌ Reject]` `[🔍 詳細]` |
| 2.6.B.6 | Interactions endpoint 実装: `POST /webhooks/discord/interactions`、Ed25519 署名検証（PyNaCl） |
| 2.6.B.7 | ボタン押下ハンドラ：Approve / Reject → Approval テーブル記録 → LangGraph Platform に resume |
| 2.6.B.8 | Modify ボタン → Discord Modal 表示 → 編集 draft で記録 → resume |
| 2.6.B.9 | チャンネルルーティング：`AgentDefinition.config.discord_channel_key` から webhook を解決 |
| 2.6.B.10 | スマホ実機 E2E：通知 → タップ → 承認 → AxonRelay 完了 |

### 2.6.C: 運用考慮

- 通知の rate limit（Discord webhook 30req/min/channel）→ バースト時は queue 化
- `#audit` チャンネルは bot 以外の書き込みを禁止 + Audit Log 連携
- Bot Token / Public Key は **絶対に** リポジトリにコミットしない（`.env` で管理、`.gitignore` 確認）

**完了条件:** 寝起きのスマホで「タスク #N が承認待ち」通知 → タップ → ボタンで承認 → 数秒後に Platform で resume されるところまでが手元で確認できる。

| リスク | 対策 |
|---|---|
| Platform 側の graph と Backend の状態同期がずれる | Platform を single source of truth にし、Backend の Draft/Approval は **イベント駆動の射影**として再構築 |
| MCP サーバのスキーマと HTTP API の重複ドリフト | 内部 service 層を共通化、両者は薄い presentation layer |
| Cloudflare Tunnel 切替時に DNS キャッシュで一部到達不能 | 切替前に TTL 短縮、夜間に切替 |
| Platform の料金 (個人 PoC で過剰になる) | 利用量を確認し、必要なら self-hosted LangGraph Server (Docker 単体) に逃げる |
| MCP の認可（誰でも tool を呼べる状態は危険） | 個人ローカルなら stdio transport、リモートなら Tailscale 経由に限定 |

---

## マイルストーン順序の依存

```
2.1 (掃除) ──┬─► 2.2 (Platform) ──┐
             │                     ├─► 2.4 (ホスティング切替) ──┬─► 2.5 (ダッシュボード)
             └─► 2.3 (MCP)         ┘                            │
                                                                └─► 2.6 (Discord)

- 2.1 → 2.2 / 2.3（順序依存）
- 2.2 と 2.3 は並列可能
- 2.4 は 2.2 と 2.3 の両方が終わってから
- 2.5 と 2.6 は 2.4 後に並列可能（2.5 はローカルで先行可、2.6 は Discord ゼロベース構築 (2.6.A) のみ並行先行可）
- 2.6.A（Discord 側のゼロ構築）は 2.4 を待たずに着手しても良い — Bot Token と Webhook URL を先に控えておけば後で接続するだけ
```

---

## Phase 3: 調整レイヤー（2026-08-30 実装済み）

Step 2 の完了後、dogfood の実態が §2 の想定（self + 2〜3 AI、1 台）を超えた — 複数デバイス・
複数リポジトリ・**同一リポジトリの複数 clone** で Claude と Codex が並行して動く形になった。
台帳が答えない問い（誰がどこで何を触っているか / これから編集する場所は空いているか /
別 clone にどう伝えるか）を AxonRelay 自身の機能として引き取った。

| # | タスク | 状態 |
|---|---|---|
| 3.1 | Workspace / Session / Claim / Relay / RelayReceipt スキーマ + migration 006 | ✅ |
| 3.2 | パス重なり判定（`app/territory.py`）— 保守的・見逃さない | ✅ |
| 3.3 | 調整サービス層（`app/coordination.py`）— 在席 / claim / relay / board | ✅ |
| 3.4 | MCP tools 10 本 + `axonrelay://board` resource | ✅ |
| 3.5 | REST 読み取り `/coordination/*`（ダッシュボード・人間用） | ✅ |
| 3.6 | **§11.6 解消** — `record_approval` に task 行ロックを入れ並行承認を直列化 | ✅ |
| 3.7 | MCP Streamable HTTP transport（複数デバイスで 1 インスタンス共有） | ✅ |
| 3.8 | mcp SDK 2.x 移行（`FastMCP` → `MCPServer`）+ バージョンピン + import を守る CI テスト | ✅ |
| 3.9 | ダッシュボードにボード表示を足す | 未着手 |
| 3.10 | `focus` の自動更新（IDE フックから heartbeat） | 未着手 |

仕様: [coordination-spec.md](./coordination-spec.md)。メッセージブローカーを入れない判断:
[adr-006-no-message-broker.md](./adr-006-no-message-broker.md)（issue #21 / #38 / PR #35 の打ち切り理由を含む）。

---

## オープン論点（Step 2 着手前に決めたい）

1. **MCP サーバを Backend と同居させるか分離するか？**
   - 同居: Python `mcp` SDK で `main.py` 隣に。シンプル
   - 分離: 独立プロセス（Node or Python）。Backend を MCP から守れる
   - **推奨: 同居**（個人 PoC、コード重複避け）

2. **Backend のホスト先：自宅 PC か小規模 VPS か？**
   - 自宅 PC + Cloudflare Tunnel: $0、ただし PC を起こしておく必要
   - 小規模 VPS (Hetzner CX11 € 4.5/月): always-on
   - **推奨: 自宅 PC + Tunnel** で開始、安定運用したくなったら VPS に移行

3. **LangGraph Platform の有料プランか self-hosted か？**
   - Platform: 個人プランあり（無料枠あり）
   - Self-hosted: Docker 1 コンテナで動く、ただしダッシュボード機能は限定
   - **推奨: Platform 個人プラン**（無料枠で足りるはず、足りなくなったら考える）

4. **AG-UI ダッシュボードのフレームワーク：Next.js 継続か Vite + React に変えるか？**
   - 既存 Next.js コードは承認 UI 系を全削除する前提で骨だけ残す価値ある？
   - **推奨: 一度全部捨てて Vite + React + CopilotKit で新規作成**（既存コードに引きずられない）

---

## 着手の入り口

最初に手を付けるのは **Phase 2.1.1（User/Project drop マイグレーション作成）** から **2.1.4（モデル削除）** まで。ここが終わると schema が固まり、Backend の他のレイヤを安全に削れる。

**並行して時間ができたら進められるもの:**
- 2.6.A.1〜2.6.A.10（Discord ゼロベース構築）— Backend 実装を待たずに Discord 側の準備が完結。Bot Token / Public Key / Webhook URL を `.env` 用にメモしておけば、後で接続するだけになる。
