# AxonRelay / core

日本語 | **[English](README.md)**

**人＋AI 混成チームのためのガバナンス台帳と調整ボードを、MCP サーバとして公開する。**

[![CI](https://github.com/AxonRelay/core/actions/workflows/ci.yml/badge.svg)](https://github.com/AxonRelay/core/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776ab.svg)](.python-version)

**30 秒でわかる**

- **何か** — コーディングエージェントが話しかける MCP サーバ。*誰が何を承認したか*の
  改ざん検出可能な記録と、*いま誰がどこを編集しているか*の共有ボードを、複数マシン・
  複数リポジトリ・複数 clone をまたいで持つ。
- **誰向けか** — 複数のエージェント（Claude Code、Codex、…）を並行して走らせている
  一人の開発者。互いの上書きを止めたい、人間が実際に承認した証跡が欲しい。
- **何が要るか** — ボードと台帳の読み取り側には Docker と Python 3.12 だけ。エージェントに
  タスクを*実行*させるときだけ LangGraph Platform のデプロイが要る。前者に API キーは
  不要 — [Quick Start](#quick-start) を参照。
- **何ではないか** — エージェントランタイムでも、承認 UI でも、メッセージバスでもない。
  それらは意図的に LangGraph Platform・MCP elicitation・AG-UI に委譲している —
  [理由](#なぜ存在するのか2026-年中盤の文脈)。

AxonRelay は「**誰が**（人か AI か）、**どのドラフト版**に対して、**何をしたか**、そして
**誰が・どんなコメントで・いつ承認/差戻したか**」を記録する。加えて、複数のマシン・
複数のリポジトリ・**同一リポジトリの複数 clone** で複数のエージェントが同時に動き出した
瞬間に発生する、決定の**手前**の問い——**いま誰がどこで何を触っていて、これから衝突しないか**
——にも答える。

エージェントランタイム、人間の介入（承認待ち）、UI はすべて標準（LangGraph Platform /
MCP / AG-UI）に委譲する。AxonRelay が自分で持ち続けるのは、それらの標準が**提供しない**
部分——**永続的で、セッションをまたぎ、Actor をまたぐ台帳と、共有された調整ボード**だけ。

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

規制も同じ方向を向いているが、よく言われるより時期は遅く、範囲は狭い。EU AI Act が
*高リスク*システムに課す要件には自動イベント記録（第 12 条）と人間による監督（第 14 条）が
含まれ、承認台帳が記録するものと主題が重なる。ただしこの高リスク義務は 2026-08-02 時点で
**発効していない**。同日は法の一般適用日であり透明性規則（第 50 条）の開始日であって、
高リスク義務は Digital Omnibus on AI（Regulation (EU) 2026/1744、2026-07-27 発効）により
**2027-12-02**（Annex III システム）と **2028-08-02**（Annex I システム）へ延期された。
日付・一次資料・最終確認日は [docs/regulatory-positioning.md](docs/regulatory-positioning.md) にある。

**AxonRelay はコンプライアンス製品ではなく、認証の仕組みでもない。** 何も認証せず、
EU AI Act やその他の規制への対応を何ら提供しない。台帳は改ざん検知可能であって、
法的な証明力を持つものではない。このリポジトリが保持し dogfood するのは、それらの要件と
まっとうなエンジニアリングが共有する一つの考え——人間は永続的な台帳の **第一級 Actor**
であり、interrupt 境界の外側の例外ではない——である。

---

## アーキテクチャ

```
  Claude @ clone A        Codex @ clone B        Claude @ 別リポジトリ
  (ラップトップ)           (デスクトップ)          (ラップトップ)
      │                       │                       │
      └───────────────────────┼───────────────────────┘
        MCP（ローカルは stdio / Tailscale・Tunnel 経由の Streamable HTTP）
                              ▼
              AxonRelay Backend（FastAPI + MCP サーバ同居）
                 │   - MCP : 26 tools + 3 resources（メインインターフェース）
                 │   - REST: /tasks /agents /coordination（読み取り中心）
                 │   - Postgres: 台帳 ＋ 調整ボード
                 │
                 └──▶ LangGraph Platform（runtime / checkpoint / observability）
                          └──▶ writer → reviewer → [interrupt] → finalize
                                   └──▶ LLM (Claude / GPT)
```

| 構成要素 | 役割 |
|---------|------|
| **MCP サーバ** | メインインターフェース。IDE からタスク作成・承認・差戻し。backend と同居（[`backend/app/mcp/`](backend/app/mcp/)）。 |
| **Backend (FastAPI)** | 薄い REST 層 ＋ 台帳を所有する SQLAlchemy service 層。 |
| **PostgreSQL** | 台帳（Actor / TaskAssignment / Draft（版付き）/ Approval / ExternalLink — Platform thread state の射影）**と** 調整ボード（Workspace / Session / Claim / Relay）。 |
| **調整ボード** | 在席状況・助言的な territory claim・エージェント間の永続メッセージ（[`app/coordination.py`](backend/app/coordination.py)）。pull 型で、誰の作業も中断しない。 |
| **LangGraph Platform** | エージェントランタイム・checkpoint・observability。graph は [`axonrelay-graph/`](axonrelay-graph/)。（現在は *LangSmith Deployment* に改称。Aegra 等で self-host も可。） |

backend は **読み取り中心**の設計: 書き込み（作成・承認・差戻し）は MCP 経由が一次経路で、
REST API は主にダッシュボードから台帳を読むためにある。

---

## データモデル — 台帳

| モデル | 役割 |
|-------|------|
| `Actor` | 人と AI を同型で扱う統一抽象。人間 Actor（`name="self"`）1 件がオペレータ。AI Actor は `AgentDefinition` と 1:1。 |
| `TaskAssignment` | Actor を Task にロール付きで紐付け: `executor` / `reviewer` / `approver` / `observer`。 |
| `Draft` | タスク成果物の版履歴——承認が束縛される **artifact**。`(task_id, version)` は unique。各版は `commitment`（本文バイト列の SHA-256）と、分かる場合は `producer_actor_id` を持つ。 |
| `Approval` | 追記専用の記録: `action`（approved/rejected）、`comment`、`reviewer_actor_id`、タイムスタンプ、**そして判断対象の artifact**（`artifact_ref` / `artifact_version` / `artifact_commitment` / producer）。per-task の SHA-256 hash chain（`prev_hash` / `entry_hash`）が束縛フィールドまで覆う——[`app/ledger.py`](backend/app/ledger.py) と [ADR-009](docs/adr-009-artifact-commitment.md)。 |
| `ExternalLink` | 外部成果物へのリンク（将来の MCP リソース URI など）。 |

ステートマシン:

```
DRAFT → WAITING_REVIEW → WAITING_APPROVAL → APPROVED → COMPLETED
            │                    │
            └─► NEEDS_REVISION ◄─┘ ─► DRAFT / CANCELLED
```

台帳は並行書き込みに対して安全。`record_approval` は chain の先端を読む前に task 行の
ロックを取るため、2 つのエージェントが同一 task を承認しても hash chain は分岐しない。
同じロックが draft の追加も覆うので、修正 draft 付きの承認は先に新版を作り、その版に束縛される。

混同しやすい 3 つの保証:

- **イベントの改ざん検知** — 承認の編集や並べ替えは、artifact 束縛フィールドの改変も含めて `verify_task_ledger` で検出される。
- **artifact の同定** — 新しい承認はすべて、判断した draft の版番号と本文 commitment を名指しする。差し替わった draft への判断は拒否される（`409` / `stale_decision`）。保存中の draft 本文がその commitment と今も一致するかは別のチェックであり、台帳は外部 artifact の内容を保存しない。束縛導入前の記録はそのまま検証され、`not artifact-bound` として報告される。
- **規制グレードの署名・タイムスタンプ・否認防止** — 対象外。[docs/regulatory-positioning.md](docs/regulatory-positioning.md) を参照。

---

## データモデル — 調整ボード

| モデル | 役割 |
|-------|------|
| `Workspace` | 1 台のマシン上の、1 リポジトリの、1 clone。同一性は `(host, repo, clone_path)`。同じ repo の 2 つの clone は別の Workspace。 |
| `Session` | ある Actor が、ある Workspace で作業している期間。claim を持ち relay を受け取る単位。再登録で同じセッションを再開するので、エージェントが落ちても失われない。 |
| `Claim` | **助言的で期限付き**のリース。repo 内のパス、またはパスで表現できない共有 git 資源（`worktree` / `stash` / `refs` / `remote`）に対して取る。重なる claim は既定で拒否（`force` で上書き可、上書きは記録される）。 |
| `Relay` | 「観客」宛の永続メッセージ — 特定 Actor / 特定 clone / 特定 repo / 全体。pull で配信。 |
| `RelayReceipt` | 受信者ごとの既読・ack 状態。1 人が ack してもブロードキャストが他の全員から消えない。 |

設計・意味論・エージェントがターンごとに従うプロトコル:
**[docs/coordination-spec.md](docs/coordination-spec.md)**。

---

## インターフェース

### MCP サーバ（一次）

**台帳系 14 tools**: `list_tasks`, `create_task`, `get_task`, `run_task`,
`list_pending_approvals`, `approve_task`, `reject_task`, `review_pending_task`
（MCP elicitation による対話的承認）, `verify_task_ledger`, `get_drafts`,
`list_agents`, `create_agent`, `update_agent`, `get_self_actor`。

**調整系 12 tools**: `register_session`, `heartbeat_session`, `end_session`,
`get_board`, `check_conflicts`, `claim_territory`, `release_territory`,
`claim_git_resource`, `check_git_resource`, `send_relay`, `read_inbox`, `ack_relay`。

`refs/stash` はリポジトリ単位の ref なので、**兄弟 worktree が1つの stash スタックを共有する** —
片方の `git stash pop` が、もう片方が退避した作業を奪える。守るべきパスが存在しない。
[`tools/gitsafe`](tools/gitsafe) が機械的に止める: stash に所有セッションのタグを刻み
（オフラインでも機能）、破壊的 git の前にボードへ照会する。詳細は
[coordination-spec.md §4.3–4.4](docs/coordination-spec.md)。

**3 resources**: `axonrelay://board`, `axonrelay://tasks/{id}`,
`axonrelay://tasks/{id}/drafts/{version}`。

**Safe Envelope — 2 tools**: `ingest_safe_envelope`, `list_safe_events`。
`AXONRELAY_SAFE_MODE=1` にするとインスタンスは content-blind になる: 上記のうち自由文を
受ける tool はすべて固定文言で拒否し、envelope——opaque id・閉じた enum・producer が算出した
artifact commitment・timestamp だけ——が唯一の書き込み経路になる
（[ADR-010](docs/adr-010-safe-envelope.md)、[schema](docs/schemas/safe-envelope-v1.json)）。
フラグ無しでは全文の個人 PoC のまま。

**identity と scope。** `AXONRELAY_REQUIRE_AUTH=1` にすると、HTTP 呼び出し元は
credential（`python -m app.credentials issue`）を提示する。credential はサーバが記録する
Actor を名指し、scope を持つ。全 tool・全 REST route が scope に対応づけられており、
未対応の surface があると構造テストが落ちる（[ADR-011](docs/adr-011-caller-identity.md)）。
未設定のときと stdio ではオペレータとして走る。

tool リファレンスと Claude Code 設定: **[docs/mcp-server.md](docs/mcp-server.md)**。

`mcp>=2.1,<3` が必要 — 2.0 で `FastMCP` が `MCPServer` に改称されたため、依存はピン止めしている。

### REST API（読み取り中心・ダッシュボード用）

backend を起動すると `http://localhost:8000/docs` に OpenAPI の対話 UI が出る
（`docker compose up -d backend`、または `backend/` で `uvicorn app.main:app --reload`）。

| Method | Path | 説明 |
|--------|------|------|
| `GET` | `/` | ヘルスチェック |
| `GET` | `/actors` `/actors/{id}` `/actors/me` | Actor |
| `GET`/`POST`/`PUT`/`DELETE` | `/agents` `/agents/{id}` | AI エージェント定義 |
| `GET`/`POST`/`PUT`/`DELETE` | `/tasks` `/tasks/{id}` | タスク |
| `POST`/`GET` | `/envelopes` | Safe Envelope の取り込みと一覧（content-blind。`AXONRELAY_SAFE_MODE` 下では唯一の書き込み経路） |
| `POST` | `/tasks/{id}/run` | Platform で interrupt/完了まで実行 |
| `GET` | `/tasks/pending/approvals` | 統一承認受信箱 |
| `POST` | `/tasks/{id}/approve` `/tasks/{id}/reject` | 承認 / 差戻し |
| `GET` | `/tasks/{id}/drafts` | ドラフト履歴 |
| `GET` | `/tasks/{id}/ledger/verify` | 承認 hash chain の改ざん検証 |
| `GET`/`POST`/`DELETE` | `/tasks/{id}/assignments` | タスク割り当て |
| `GET` | `/coordination/board` | 稼働セッション / 有効な claim / 未 ack の relay |
| `GET` | `/coordination/sessions` `/coordination/claims` | 誰がどこで作業中か / 何が claim されているか |
| `GET` | `/coordination/sessions/{id}/inbox` | セッションの relay 受信箱 |

書き込み系は MCP 側からも公開され、IDE からはそちらが一次経路。

---

## Quick Start

**必要なもの**

| やりたいこと | 必要なもの |
|---|---|
| 調整ボードと、台帳の読み取り側 — `register_session` / `claim_territory` / `send_relay` / `list_tasks` / `verify_task_ledger`、ダッシュボード | Docker（Postgres 用）と Python 3.12。**API キーは不要** |
| タスクの作成・実行、ドラフトの承認・差戻し — `create_task` / `run_task` / `approve_task` / `reject_task` | [`axonrelay-graph/`](axonrelay-graph/) を LangGraph Platform にデプロイしたものと LLM キー（`.env` の `LANGGRAPH_*` と `ANTHROPIC_API_KEY`） |

タスク作成は Platform の thread を確保し、承認は thread を再開するため、この
4 つは Platform を設定するまで `PlatformNotConfiguredError` で明示的に失敗する。
それ以外は最初の 1 分から動く。

**5 コマンド**

```bash
cp .env.example .env                    # ラップトップならそのままで動く。LANGGRAPH_* はあとで
docker compose up -d postgres           # Postgres を 127.0.0.1:5432 に
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
(cd backend && alembic upgrade head)    # migration 011 まで適用・"self" Actor を seed
```

venv を有効にした状態なら `make dev` で後半 3 つと MCP サーバの起動をまとめて行える。
`make help` で残り（`api` / `test` / `lint` / `frontend`）が出る。

`.env` は自動で読まれる。`DATABASE_URL` は `localhost` を指しており、compose
ネットワークの `postgres` というホスト名はコンテナの中でしか使われない。

**Claude Code をつなぐ**

リポジトリ直下に project scope の [`.mcp.json`](.mcp.json) を同梱している。venv を
有効にしたシェルでリポジトリのルートから起動すると、`axonrelay` サーバを有効にするか
聞かれる:

```bash
claude
```

他のクライアント、複数デバイス向けの Streamable HTTP transport、tool リファレンス:
[docs/mcp-server.md](docs/mcp-server.md)。調整ボードが意味を持つには、**複数デバイスが
1 つのインスタンスを共有している**必要がある。`--http` で起動し、Tailscale 経由で
到達させる。このトランスポートには呼び出し元認証がないため、private network に
留めること（手順は **[deploy/DEPLOYMENT.ja.md](deploy/DEPLOYMENT.ja.md)**）。

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
| `AXONRELAY_REQUIRE_AUTH` | — | `1` で HTTP 呼び出しに per-caller credential を要求し、記録する Actor を credential 側で決める（[ADR-011](docs/adr-011-caller-identity.md)）。stdio は loopback 信頼のまま |
| `AXONRELAY_SAFE_MODE` | — | `1` でインスタンスを content-blind にする: 自由文を受ける surface は拒否、Safe Envelope が唯一の書き込み経路（[ADR-010](docs/adr-010-safe-envelope.md)） |
| `AXONRELAY_SAFE_PUBLIC_IDENTIFIERS` | — | `1` で `identifier_policy: public`（`owner/repo` slug）の envelope を受理。それ以外は opaque id 必須 |

データベースのセットアップとマイグレーションは [SETUP_POSTGRES.md](SETUP_POSTGRES.md) を参照。

---

## 現状

ピボットは完了。Phase 3（調整レイヤー）まで入っている:

- ✅ **Backend**: Actor ベースの台帳、MCP サーバ（28 tools / 3 resources）、LangGraph Platform クライアント、migration 011 まで。承認台帳は改ざん耐性あり（per-task SHA-256 hash chain、`verify_task_ledger` で検証）、かつ並行書き込みに対して安全 — [delta-mvp-spec §11.6](docs/delta-mvp-spec.md) が記録していた単一書き込み者前提は解消済み。
- ✅ **調整レイヤー (Phase 3)**: Workspace / Session / Claim / Relay。MCP から駆動し `/coordination/*` で読む。複数マシン・複数リポジトリ・兄弟 clone にまたがるエージェントが、互いを認識し、同じパスの同時編集を避け、永続メッセージを残せる — [docs/coordination-spec.md](docs/coordination-spec.md)。
- ✅ **Graph**: `axonrelay-graph/`（writer → reviewer → human_approval → finalize）が Platform 用に準備済み。
- ✅ **Frontend**: 薄い**読み取り専用**ダッシュボード（Vite + React + TS・[`frontend/`](frontend/)）。タスク一覧（status filter）/ ドラフト履歴 / 承認 timeline / task ごとの台帳検証バッジ。書き込みは MCP/IDE 経路のまま。（CopilotKit/AG-UI は読み取り専用には不要なため見送り。）
- 🚧 **Infra**: 旧 `infra/`（AWS EC2 DNS）と `Caddyfile` を撤去済み。Cloudflare Tunnel + Tailscale + Vercel/Pages への切替はテンプレ化＋**[deploy/DEPLOYMENT.ja.md](deploy/DEPLOYMENT.ja.md)** に手順化（DNS 切替・EC2 解約などアカウント側操作は手動のオペレータ作業）。**調整ボードを実際に使うには同ドキュメント §3（Tailscale + 共有 MCP エンドポイント）が必要** — コードは入っているが、まだどこでも稼働していない。
- 🚧 **Tests**: SQLite 329 ケース（台帳の不変条件・並行書き込み安全性・run-state 投影の idempotency・調整レイヤー・パス重なり判定・git 資源 claim・MCP tool surface）に加え、実マイグレーションを適用し実コネクションで承認台帳と資源 claim を競合させる Postgres スキーマ整合 29 ケース。両方 CI で実行（Postgres ジョブは `postgres:16` サービス）。ローカルでは `AXONRELAY_TEST_POSTGRES_URL=... pytest tests/test_postgres_schema.py`。

ロードマップと移行計画: [docs/step2-plan.md](docs/step2-plan.md)。ピボットの背景とスコープ: [docs/delta-mvp-spec.md](docs/delta-mvp-spec.md)。調整レイヤーの設計: [docs/coordination-spec.md](docs/coordination-spec.md)。

---

## ドキュメント

- [docs/delta-mvp-spec.md](docs/delta-mvp-spec.md) — ピボット仕様（Actor モデル / スコープ / dogfood シナリオ）
- [docs/regulatory-positioning.md](docs/regulatory-positioning.md) — AxonRelay が*何でないか*（コンプライアンス・認証の主張はしない）、EU AI Act の適用日と一次資料と最終確認日、docs レビューチェックリスト（英日併記）
- [docs/coordination-spec.md](docs/coordination-spec.md) — Phase 3: 在席・territory claim・relay
- [docs/adr-006-no-message-broker.md](docs/adr-006-no-message-broker.md) — 調整レイヤーにメッセージブローカーを入れない理由
- [docs/adr-007-gitsafe-enforcement-path.md](docs/adr-007-gitsafe-enforcement-path.md) — `gitsafe` を PATH ラッパにせず明示 opt-in に留める理由
- [docs/adr-009-artifact-commitment.md](docs/adr-009-artifact-commitment.md) — 承認が判断対象の draft 版と本文 commitment を名指しする理由と、それが証明しないこと
- [docs/adr-010-safe-envelope.md](docs/adr-010-safe-envelope.md) — content-blind モード: Safe Envelope が受け付けるもの、他の surface が拒否するもの、拒否された値を保存・ログ・エラーに残さない仕組み
- [docs/adr-011-caller-identity.md](docs/adr-011-caller-identity.md) — per-caller credential と 6 つの scope: 記録される Actor がリクエストパラメータでなくなる理由と、stdio を loopback 信頼のままにする理由
- [docs/step2-plan.md](docs/step2-plan.md) — 移行計画（Phase 2.1–2.6）
- [docs/mcp-server.md](docs/mcp-server.md) — MCP サーバ接続ガイド & tool リファレンス
- [docs/discord-setup-guide.md](docs/discord-setup-guide.md) — Discord モバイル承認セットアップ（アカウント側の手順のみ。backend 側は未実装）
- [deploy/DEPLOYMENT.ja.md](deploy/DEPLOYMENT.ja.md) — **デプロイ手順（日本語）**。ホスティング切替と、調整ボードに必要な共有 MCP エンドポイントの立て方（[English](deploy/DEPLOYMENT.md)）
- [SETUP_POSTGRES.md](SETUP_POSTGRES.md) — PostgreSQL セットアップ & マイグレーション

---

## ライセンス

MIT - [LICENSE](LICENSE) を参照。個人 PoC であり、MCP transport には呼び出し元認証が
ない。private network の内側で動かす前提のコードなので、そのつもりで扱うこと。
