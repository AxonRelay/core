# ADR-006: 調整レイヤーにメッセージブローカーを導入しない (NATS 作業の打ち切り)

> 決定: 2026-08-30 / Status: 採用 / 関連: issue #21, issue #38, PR #35

---

## 文脈

2026-02 時点で、backend を NATS JetStream ベースの Pub/Sub マイクロサービス（API Node + Worker Node の分離）へ移行する作業が進んでいた（issue #38）。動機は妥当なもので、当時の `main.py` は FastAPI のエンドポイントと LangGraph の実行を同一プロセスで同期実行しており、

- リクエストが LLM 呼び出しでブロックする
- ワーカーを独立にスケールできない
- 失敗時の再試行が難しい

という課題があった。`app/api.py` / `app/worker.py` / `app/nats_client.py` / `app/redis_cache.py` / `app/messages.py` / `app/config.py` が書かれ、`docker-compose.yml` に NATS サービスと worker replica が足された。

この作業は**コミットされないまま中断**し、その間に main 側で δ ピボット（[delta-mvp-spec.md](./delta-mvp-spec.md)）が完了した。

## 決定

**NATS JetStream を導入しない。中断していた実装はマージせず、`archive/nats-jetstream-wip` ブランチに退避して打ち切る。**

issue #21 / #38 は superseded として close。PR #35（Gemini 統合）も同様 — LLM 呼び出しは `axonrelay-graph/agent/llm.py` に移り、既定プロバイダは Anthropic になっている。

## 理由

### 1. 前提となるコードベースが存在しない

退避した実装は、ピボットが削除した層の上に建っている:

| WIP の前提 | ピボット後 |
|---|---|
| `app/worker.py` が `app/graph.py` を実行する | `graph.py` は削除。ランタイムは LangGraph Platform に委譲 |
| Redis を checkpoint / 状態キャッシュに使う | Redis 削除。checkpoint は Platform 側 |
| `app/api.py` は旧 `main.py` のフォーク（`/projects` `/auth/sync` を含む） | migration 003 で User / Project ごと drop |

移植ではなく書き直しになる。

### 2. 解こうとしていた問題を、もう自分で解いていない

「LLM 呼び出しが長時間ブロックする」「ワーカーを独立にスケールしたい」「再試行したい」— これはエージェントランタイムの問題であり、ピボットの核心はまさに**その層を自前で持たない**という決定だった。実行・チェックポイント・再試行・可観測性は LangGraph Platform の責務。ブローカーを入れ直すことは、ピボットが下した判断を静かに巻き戻すことになる。

### 3. 調整レイヤーが必要とするのは push ではなく pull

Phase 3（[coordination-spec.md](./coordination-spec.md)）で複数エージェント間の通信が必要になったので、「結局ブローカーが要るのでは」は再検討に値した。要らない、というのが結論である。

調整レイヤーの配信モデルは意図的に**準同期**である — エージェントは自分のターンの頭で受信箱を読む。相手を割り込ませない。この形にはブローカーの中心的な機能（低レイテンシの push 配信、consumer group、at-least-once の再配送）がどれも要らない。必要なのは、全員が読める永続テーブルだけで、それは既に Postgres にある。

NATS を入れれば、運用するインフラが1つ増え、Postgres と NATS のあいだで状態の二重管理が生まれる。個人 PoC で払う対価としては見合わない。

### 4. 規模が合っていない

想定される同時セッションは数個〜十数個、メッセージは1日あたり数十件のオーダー。Postgres の1テーブルで数桁の余裕がある。

## 不採用にした代替案

| 案 | 不採用の理由 |
|---|---|
| WIP を新アーキテクチャへ移植 | 上記1。移植ではなく書き直しであり、かつ2の理由で書き直す価値がない |
| Postgres `LISTEN` / `NOTIFY` で push を足す | 準同期という設計選択に反する。ボードは pull で足りており、接続を張り続ける前提はエージェントの走り方（バースト的）に合わない |
| Redis Streams（Redis を戻す） | ピボットが外したインフラを、NATS より小さいというだけの理由で戻すことになる |

## 影響

- `archive/nats-jetstream-wip` ブランチは参照用として残す。main にはマージしない
- 将来スループットが Postgres の1テーブルで足りなくなったら、この ADR を見直す。その閾値は「調整メッセージが毎秒オーダーになったとき」であり、現状から3〜4桁離れている
