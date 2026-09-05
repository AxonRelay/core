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

### 4.3 Git 資源 — パスで表現できない共有物

パス claim は「誰がどのファイルを編集するか」に答える。しかし checkout が**ちょうど1つずつ持つ可変の共有物**は、どんなパターンでも表現できない:

```
git stash pop   ← パスを1つも指名しない
```

実測で確認した事実（2026-08-31）:

```
A が clone-a で git stash push      → stash@{0}: agent-A wip
B が 別 worktree で git stash pop   → 成功。A の作業が B のツリーへ
A から見た stash                    → 空
A の作業ツリー                      → 元に戻った（作業が消えた）
```

**`refs/stash` はリポジトリ単位の ref なので、同じ clone の全 worktree が1つの stash スタックを共有する。** 「共有 clone をやめて worktree にする」という定石はこの事故を防がない。

#### 資源と、その衝突範囲

| 資源 | 実体 | 衝突する範囲 | 同一性 |
|---|---|---|---|
| `worktree` | 作業ツリー / index / HEAD | その checkout のみ | `workspace` |
| `stash` | stash スタック | **同じ `.git` の全 worktree** | `(host, git_dir)` |
| `refs` | ローカルの branch / tag | 同上 | `(host, git_dir)` |
| `remote` | リモートの refs（force push / `+refspec` / `push --delete` の着地点） | **その repo の全 clone・全 host** | `repo` |

stash と refs については `repo` では広すぎ（別 clone は独立）、`workspace` では狭すぎる（兄弟 worktree を見逃す）。`remote` だけは逆で、どの clone からでも同じリモート refs に着地するため `repo` が正しい境界になる。
**`git rev-parse --git-common-dir` が stash の正しい同一性**であり、`Workspace.git_dir` に記録する。

`git_dir` が不明な場合は `(host, repo)` にフォールバックして**過剰報告**する — パス判定と同じく、見逃すより多めに報告する側に倒す。

#### `force` の意味

`force=true` は「いまから自分が保持者である」という宣言なので、押し退けた claim は `RELEASED` に遷移させる（削除はしない）。放置して `HELD` のままにすると、押し退けた側が guard に拒否され続け、ボードには排他 claim の保持者が2人並ぶ。誰を押し退けたかは新 claim の `forced_over` に残り、押し退けられた側からも見える。パス claim と資源 claim で共通。

#### `git_dir` の取り方と正規化

`register_session` には次の値を渡す:

```bash
git rev-parse --path-format=absolute --git-common-dir   # git 2.31+
```

git 自身の出力は一貫していない — main worktree では相対の `.git`、linked worktree では絶対パスを返す。サーバ側は相対値を `clone_path` 基準で解決し、`..` / `//` / 末尾スラッシュを正規化してから比較する（`normalize_git_dir`）。symlink はサーバでは解決できないので、checkout が symlink 配下にある場合はクライアントが `realpath` を通す。

#### 同時 claim の直列化

claim の付与は「生きている claim を読む → 衝突がなければ insert」の2段階で、2つのトランザクションが同時に「空いている」と読めば**両方に排他 claim が付与されてしまう**。パス claim と資源 claim の両方で、同じ repo の `workspaces` 行を id 順に `SELECT ... FOR UPDATE` してから衝突判定する（`_lock_repo_workspaces`）。承認台帳が task 行をロックするのと同じ形。Postgres parity テストが 8 セッション同時の `stash` claim で granted が1つだけになることを検証する（ロックを外すと 8 つ granted になる）。

MCP tool: `claim_git_resource(session_id, resource, ...)` / `check_git_resource(session_id, resource)`。

---

### 4.4 強制層 — `tools/gitsafe`

**claim は助言的であり、ハルシネーションしているエージェントは助言を読まない。** 「事故を無視できる程度まで」という要求に対しては、判断に依存しない機械的な層が要る。

[`tools/gitsafe`](../tools/gitsafe) は git の破壊的操作の手前に立つ fail-closed なラッパで、2層構成:

**① stash 所有者タグ（AxonRelay 不要）**

```
gitsafe git stash push -m "wip"
  → stash@{0}: On main: [axonrelay:s12] wip
```

`pop` / `apply` / `drop` は、タグが自分のものでなければ拒否する。タグは stash メッセージの中にあるので、**AxonRelay が落ちていてもネットワークが無くても機能する。** 今回の事故はこの層だけでほぼ消える。

`stash clear` は clone 内の全 entry を消すため、常に拒否する。`stash store` / `stash import` は所有者タグのない entry をスタックに載せる（`store -m` は reflog メッセージにしか効かず、タグ検査が読む commit subject には入らない）ため、誰も gitsafe 経由で pop できない孤児を作らないよう拒否する。

**② AxonRelay の資源 claim**

`reset --hard` / `clean`（dry-run 以外）/ dirty な `checkout` / `rebase` / `branch -D`（`refs`）/ `push --force`・`+refspec`・`--delete`（`remote`）は、`GET /coordination/git/guard` に照会し、他セッションが握っていれば拒否する。判定は `$*` のグロブではなく引数ごとに行い、git が受け付ける長オプションの省略形（`reset --har`）も前方一致で拾う（`--follow-tags` を `-f` と誤認しない、`+main:main` を見逃さない、曖昧な省略形はガード側に倒す）。

`remote` の照会には**push の着地先**から導いた `repo`（`owner/name`）を添える。着地先は位置引数のリモート名または URL、`--repo=`、いずれも無ければ git 自身が選ぶ既定（`branch.<b>.pushRemote` → `remote.pushDefault` → `branch.<b>.remote` → `origin`）で、`origin` を固定で見ることはしない（`git push upstream --force` の claim を取り逃すため）。サーバは session が登録した repo と着地先が異なれば、着地先 repo の `remote` claim に対して**その session を他人として**判定する。未登録の呼び出し元は、この `repo` の `remote` claim があれば拒否、`repo` が導けなければ（ローカルパスのリモート等）全ての `remote` claim と衝突するものとして保守的に拒否する。

到達性とエラーは区別する。**接続できない**場合は①のみに縮退して stderr に告げる（claim は助言的）。**到達できたがエラー応答**（4xx/5xx）の場合は拒否する — エラーを許可として扱うと、あらゆるバグが迂回路になるため。

`git -C <dir> stash pop` のような git グローバルオプションはサブコマンドの前で消費し、内部の git 呼び出し全てに引き継ぐ。未知のグローバルオプションはどれがサブコマンドか推測せず拒否する（値を取るオプションを知らずに飛ばすと、本物のサブコマンドが無防備で通る）。

git alias は**展開結果で分類する**（`git -c alias.steal='stash pop' steal` や `~/.gitconfig` の alias が既定分岐に落ちないように）。展開値の分割は `eval` ではなく `xargs` で行い（クォートは解釈するが実行も展開もしない）、シェルメタ文字（`$` `` ` `` `;` `|` `&` `()` `<>`）を含む値と `!` で始まるシェル alias は拒否する。

guard への照会には `session_id` に加えて**コマンドが実際に走る場所**（`host`, `clone_path`）を常に送る。session_id は「誰が」を示すだけで「どこで」は示さない — clone B に登録した session が `git -C clone-A reset --hard` を打った場合、サーバは checkout が session の workspace と一致しないことを検出し、その clone における**未登録の呼び出し元**として判定する（誰かが握っていれば拒否）。

stash の対象は `stash@{n}` という綴りではなく**位置引数**で決める。`git stash apply <commit id>` や `git stash branch <name> <commit id>` も git は受け付けるので、綴りで判定すると先頭 entry を検査して別 entry を適用してしまう。

**読み取り系（`status` `diff` `log` `show` `stash list`）は常に素通し。**

**原子性の限界。** 所有者チェックと git の変更は2ステップで、その間に gitsafe を通さない裸の `git stash push` が割り込めばスタックはずれる。`pop` / `apply` / `drop` は index（`stash@{0}`）ではなく commit id で操作し、`drop` は直前に id を再確認することで窓を git が許す限界まで狭めている。残る窓は、gitsafe を使う書き手は claim で排除されていること、誤って drop しても commit はオブジェクトストアに残ることをもって受容する。`stash branch` だけは git が reflog 参照しか受けないため commit に固定できず、直前の再確認のみ。

#### サブエージェントをどう扱うか

サブエージェントは親と同じ作業ディレクトリで動くので、AxonRelay に登録されていない git 実行者が clone の中に湧く。

**禁止ではなく制御を採る。** 理由:

1. 禁止は粒度が粗い。`status` / `diff` / `log` は安全で、むしろ読ませたい
2. 禁止は迂回される。`command git` でも絶対パスでも抜けられる以上、「禁止したから安全」は幻想になり、かえって危険
3. **禁止しても事故は防げない。** 原因は stash に所有者が刻まれていないことであって、実行者が誰かではない

`session_id` を持たない呼び出し元は `(host, clone_path)` で識別し、**誰かがその資源を握っていれば拒否する**（`guard_unregistered_caller`）。親が worktree を claim したのは、まさに他の何かに邪魔されないためなので、これが正しい既定である。

既知の制約: サブエージェントは親の環境変数を継承するため、`AXONRELAY_SESSION_ID` をそのまま引き継ぐと親と区別できない。実害は軽い（親セッションとして振る舞うことになり、*別 clone* からの奪取は依然防げる）が、厳密に分けたい場合はサブセッションにも `register_session` させる。

さらなる限界: ラッパは `command git` や絶対パス指定で迂回できる。「PATH 上の `git` がラッパである」ことが前提で、`ghsafe` と同じ性質の制約。

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
| `GET` | `/coordination/git/guard` | `gitsafe` 用の可否照会（読み取り専用） |

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

既定のバインドは loopback。**このトランスポートには呼び出し元認証がない**ので、他デバイスからの到達は Tailscale か Cloudflare Tunnel を経由させる。公開バインドは想定外。手順は [deploy/DEPLOYMENT.md](../deploy/DEPLOYMENT.md)。

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
| `gitsafe` の迂回不能化 | `command git` / 絶対パスで抜けられる。PATH 上の git がラッパである前提を敷く以上のことはしない（`ghsafe` と同じ立場） |
| git 以外の破壊的操作 | エージェントがファイルを直接上書きする類は本レイヤーの範囲外 |
| A2A プロトコルでの Relay 表現 | [§11.3](./delta-mvp-spec.md) で採用候補に格上げ済みだが、まず内部モデルを dogfood してから |

---

## 11. 今後の検討

- **`focus` の自動更新** — エージェントが `heartbeat_session` を呼び忘れるとボードが腐る。IDE 側のフックで自動化できないか
- **claim の粒度** — 現状は clone 単位。worktree ごとに分けたいケースが出るか観察する
- **relay の要約** — ボードが賑やかになったら、未読の要約を LLM に作らせる余地がある
- **A2A Agent Card との対応付け** — Session を A2A の Agent インスタンスとして公開する余地
