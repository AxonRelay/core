# Phase 3 — Coordination Layer 仕様

> 作成: 2026-08-30 / Status: 実装済み (migration 006) / 前提: [delta-mvp-spec.md](./delta-mvp-spec.md)

---

## 1. 解く問題

AxonRelay の台帳は「**決定のあと**」を記録する — 誰がどの版を承認したか。Phase 3 が扱うのは「**決定のまえ**」に起きる問題である。

実際の運用は以下の形になっている:

- 複数のデバイス（ラップトップ、デスクトップ）
- 複数のリポジトリ。うち一部は互いに情報を供給し合っている
- **同一リポジトリの複数 clone**（worktree、レビュー用、別マシン上の複製）
- それぞれで **Claude と Codex が並行して**作業している

この構成で恒常的に起きるのは、台帳が答えない種類の問いである:

| 問い | Phase 2 までの答え |
|---|---|
| いま誰がどこで何を触っているか | 分からない |
| これから編集するファイルは誰かが握っていないか | 分からない。マージ時に判明する |
| 別 clone のエージェントに前提を伝えたい | 手段がない |
| さっき別マシンで決めたことを、こちらは知っているか | 知らない |

現状の運用はこれを **手動プロトコル**で埋めている — [tmp/README.md](../tmp/README.md) が定める `tmp/session-progress.md` を scp / コピペ / gist で clone 間に手で配る方式。Phase 3 はこの手動プロトコルを AxonRelay の機能として引き取る。

---

## 2. 設計の中心 — 準同期 (semi-synchronous)

**このレイヤーは誰の作業も中断しない。**

エージェントは自分のターンの頭で「いま何が起きているか」を読み、自分がすることを書く。相手の応答をブロックして待つことはない。これは妥協ではなく、コーディングエージェントの実際の走り方（バースト的・各自のクロック）に合わせた選択である。

この選択が効く理由:

- **押しつけ配信がない** — 相手が今ターン中でも壊さない
- **落ちても安全** — 受信箱を読まないエージェントは誰もブロックしない
- **再起動を跨ぐ** — 宛先は「接続」ではなく「相手が誰か」。オフラインの相手宛のメッセージは待つ
- **中央のスケジューラが要らない** — 全員が同じ Postgres を見るだけ

同期を強制する設計（分散ロック、リーダー選出、合意プロトコル）はこの規模には過剰で、かつ壊れたときに全員を止める。

---

## 3. データモデル

```
Actor ──┬── Session ──── Workspace          (誰が / どこで)
        │      │
        │      ├──── Claim                  (何を握っているか)
        │      │
        │      └──── RelayReceipt ── Relay  (何を伝えたか / 誰が見たか)
        │
        └── (既存: TaskAssignment / Draft / Approval — ガバナンス台帳)
```

| モデル | 意味 |
|---|---|
| `Workspace` | 1台のマシン上の、1リポジトリの、1 clone。同一性は `(host, repo, clone_path)`。同じ clone を再登録すると同じ行に戻るので、claim と relay の履歴が連続する |
| `Session` | ある Actor が、ある Workspace で作業している期間。claim を持ち relay を受け取る単位 |
| `Claim` | repo 内のパスに対する **助言的で期限付きの**リース |
| `Relay` | Session 間の永続メッセージ。宛先は「観客」で指定する |
| `RelayReceipt` | 受信者ごとの既読 / ack 状態 |

**`repo` は正規のリモート識別子**（例: `AxonRelay/core`）を使う。これが一致していないと、同一リポジトリの兄弟 clone が互いを認識できない。clone を区別するのは `clone_path` の方。

### なぜ `RelayReceipt` を別テーブルにするか

ブロードキャスト1件には受信者が複数いる。既読状態を `Relay` 行に持たせると、1人が ack した瞬間に他の全員から見えなくなる。受信者ごとに持てば、「誰が実際に見たか」が後から答えられる — 台帳製品としてはそこが価値になる。

---

## 4. Territory — 助言的リース

### 4.1 セマンティクス

- **助言的**: claim を持っていても物理的に書き込みは止まらない。止めるのではなく、**衝突が起きる前に見えるようにする**。半自律エージェントの群れが実際に行動できるのはこの情報である
- **既定では拒否する**: 重なる claim があれば `granted: false` を返し、claim は作られない。これが「ログ」を「調整機構」にする分岐点
- **`force=true` で上書きできる**: 人間が stale なエージェントを押しのけるのは正常な操作。上書きは `forced_over` に記録され、黙って成功はしない
- **期限がある**: 既定 60 分、上限 24 時間。落ちたエージェントが永久に territory を握ることはない
- **`exclusive` / `shared`**: reader/writer 意味論。shared 同士は共存し、exclusive はすべてと衝突する

### 4.2 パスの重なり判定

パターンは repo 相対の POSIX パス。fnmatch のワイルドカード可。ワイルドカードのないパターンは**前方一致（ディレクトリ）**として扱う — `backend/app` は `backend/app/crud.py` を覆う。リポジトリ全体は `.`。

**判定は意図的に保守的で、存在しない衝突を報告することはあっても、存在する衝突を見逃さない。** 例えば `src/**/*.py` と `src/**/*.md` は、実際には同じファイルにマッチしないが衝突として報告される。誤検知のコストは一瞥、見逃しのコストは失われた編集である。

実装と全ケースは [`backend/app/territory.py`](../backend/app/territory.py) と [`backend/tests/test_territory.py`](../backend/tests/test_territory.py)。

---

## 5. Relay — 宛先付き永続メッセージ

宛先は狭い順に:

| 指定 | 届く相手 |
|---|---|
| `to_actor_id` | その Actor の全セッション（どのマシンで動いていても） |
| `to_workspace_id` | その clone のセッション |
| `to_repo` | そのリポジトリで作業中の全セッション |
| すべて未指定 | 全体ブロードキャスト |

`kind` は `note` / `question` / `answer` / `handoff` / `warning`。`warning` は「これから他人に影響することをする」（migration chain を書き換える等）、`handoff` は作業の引き継ぎに使う。

配信は **pull**。`read_inbox` を呼ぶまで何も起きない。ack するまで受信箱に残るので、いま動けなくても失われない。

---

## 6. インターフェース

### 6.1 MCP tools（一次経路）

| Tool | 用途 |
|---|---|
| `register_session` | ボードに参加。冪等 — 同じ clone の同じ actor は既存セッションを再開する |
| `heartbeat_session` | 生存報告 + `focus`（他のエージェントに見える一行）の更新 |
| `end_session` | 離脱。保持中の claim をすべて解放 |
| `get_board` | **1コールで全体像** — 稼働セッション / 有効な claim / 未 ack の relay |
| `check_conflicts` | claim せずに衝突だけ問い合わせる |
| `claim_territory` | 編集するパスのリースを取る。衝突時は拒否 + 保持者の情報を返す |
| `release_territory` | claim を返す |
| `send_relay` | メッセージを投函 |
| `read_inbox` | 自分宛を読む（既読になる） |
| `ack_relay` | 対応済みにする |

MCP resource `axonrelay://board` は同じボードを ambient context として提供する（tool call を消費せずに常時見せたいクライアント向け）。

### 6.2 REST（読み取り専用）

書き込みは MCP が一次経路。人間とダッシュボードが同じ絵を見るための読み口:

| Method | Path |
|---|---|
| `GET` | `/coordination/board` |
| `GET` | `/coordination/sessions` |
| `GET` | `/coordination/claims` |
| `GET` | `/coordination/sessions/{id}/inbox` |

---

## 7. エージェントの運用プロトコル

各エージェントがターンの頭と終わりでやること:

```
ターン開始
  1. register_session(...)          # 初回のみ。以降は同じ session_id を使い回す
  2. get_board(repo=...)            # 誰が何をしているか
  3. read_inbox(session_id)         # 自分宛の申し送り

編集に入る前
  4. check_conflicts(repo, paths)   # 計画段階の確認
  5. claim_territory(session_id, paths, reason)
       granted: false なら → conflicts[].holder を見て
                              send_relay で相手に問い合わせる、または別の場所をやる

作業中
  6. heartbeat_session(session_id, focus="...")   # focus は常に現在形で

ターン終了
  7. release_territory(session_id=...) / end_session(session_id)
```

これは `~/.claude/shortcuts.md` の `us` / `rs` / `cs` / `as`（session-progress の更新・読み取り・territory 衝突確認・アーカイブ）が手でやっていたことと同じ形をしている。違いは、手で配る代わりに全 clone が同じ1つのボードを見る点である。

---

## 8. 複数デバイスから使う

このレイヤーが成立する条件は **全員が同じ AxonRelay インスタンスを見ていること**。デバイスごとにローカル Postgres を立てると調整は成立しない。

そのため MCP サーバに Streamable HTTP を追加した:

```bash
python -m app.mcp.server                      # stdio — ローカル IDE 用
python -m app.mcp.server --http --port 8765   # Streamable HTTP — リモート用
```

既定のバインドは loopback。**このトランスポートには呼び出し元認証がない**ので、他デバイスからの到達は Tailscale か Cloudflare Tunnel を経由させる。公開バインドは想定外。手順は **[deploy/DEPLOYMENT.ja.md](../deploy/DEPLOYMENT.ja.md)**（[English](../deploy/DEPLOYMENT.md)）。

---

## 9. 台帳側の変更 — 単一書き込み者前提の解除

[delta-mvp-spec.md §11.6](./delta-mvp-spec.md) は、承認台帳の hash chain が単一の直列書き込み者を前提にしていることを既知の制約として記録していた。`record_approval` が「直前行を読む → prev_hash を計算 → INSERT」という read-modify-write を無ロックで行うため、同一 task への並行承認が chain を分岐させ、`verify_approval_chain` が後発行を改ざんと誤検知し得る、というもの。

複数エージェントが1つのインスタンスを共有する以上この前提は成り立たない。`record_approval` は chain の先端を読む**前に** task 行のロックを取るようになった（Postgres では `SELECT ... FOR UPDATE`）。これで書き込みは task 単位で直列化される。

テスト: [`backend/tests/test_ledger_concurrency.py`](../backend/tests/test_ledger_concurrency.py)。SQLite（テスト用バックエンド）には行ロックがなく書き込みを大域的に直列化するため、ここで観測できるのは「Postgres 方言で実際に `FOR UPDATE` が出ること」と「分岐した chain が検出されること」の2点。

`review_pending_task` の TOCTOU ガード（表示した draft と一致しなければ `stale_decision` を返して記録しない）は既存のまま有効で、複数書き込み者の下ではより重要になる。

---

## 10. 意図的に対象外

| 項目 | 理由 |
|---|---|
| 強制ロック / 分散合意 | この規模には過剰。壊れたときに全員が止まる |
| リアルタイム push（WebSocket / SSE での割り込み） | 準同期という設計選択そのものに反する。ボードは pull で足りる |
| claim の自動取得（ファイル書き込みをフックして claim） | エージェントが「これから何をするか」を宣言することに価値がある。事後の自動記録では衝突を予防できない |
| coordination イベントの hash chain | 改ざん耐性が必要なのは承認記録。claim / relay は追記のみで十分 |
| MCP transport の呼び出し元認証 | 個人 PoC。ネットワーク層（Tailscale / Tunnel）で境界を引く |
| A2A プロトコルでの Relay 表現 | [§11.3](./delta-mvp-spec.md) で採用候補に格上げ済みだが、まず内部モデルを dogfood してから |

---

## 11. 今後の検討

- **`focus` の自動更新** — エージェントが `heartbeat_session` を呼び忘れるとボードが腐る。IDE 側のフックで自動化できないか
- **claim の粒度** — 現状は clone 単位。worktree ごとに分けたいケースが出るか観察する
- **relay の要約** — ボードが賑やかになったら、未読の要約を LLM に作らせる余地がある
- **A2A Agent Card との対応付け** — Session を A2A の Agent インスタンスとして公開する余地
