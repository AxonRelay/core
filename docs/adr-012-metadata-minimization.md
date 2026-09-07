# ADR-012: 調整メタデータの最小化と開示ポリシー

- Status: Accepted (2026-09-07)
- Issue: #26（Epic #32 配信順序 3）
- 関連: [ADR-010](./adr-010-safe-envelope.md)、[ADR-011](./adr-011-caller-identity.md)、
  [coordination-spec.md](./coordination-spec.md)、migration `012`

## 文脈

調整ボードの仕事は「2 つのエージェントが同じファイルを同時に編集するのを防ぐ」
ことである。その仕事に必要な情報より、ボードが実際に記録している情報のほうが多い:

- `workspaces.host` / `clone_path` / `git_dir` — マシン名と絶対パス。
  `/Users/<誰か>/workspace/...` は本人の名前とディレクトリ構成を述べる
- `sessions.focus` / `claims.reason` / `relays.subject` / `relays.body` /
  `relay_receipts.ack_note` — 自由文。作業対象の説明であり、しばしば作業そのもの
- `agent_definitions.config` — 任意 JSON。API キーが入る場所である
- `external_links.url` — 制約なしの URL

個人インスタンスではこの詳細さがボードを読めるものにしている。共有インスタンスでは、
同じ詳細さが「誰がいつどこで何をしていたか」の記録になる。ADR-010 は**書き込み**を
content-blind にしたが、**読み出し**は手つかずだった。

## 決定

### 1. 開示は境界で 1 回決める

調整プレーンのレスポンスはすべて `app/disclosure.py` の view を通る。MCP の
serializer も REST も `coordination.board()` も `find_conflicts()` も同じ関数を呼ぶ。
モードで内容が変わる:

| | full-text（既定・個人 PoC） | safe（`AXONRELAY_SAFE_MODE`） |
|---|---|---|
| Actor | `name` | `actor_ref`（opaque） |
| Workspace | `host` / `repo` / `clone_path` / `git_dir` / `label` | `workspace_ref`、`repo` は public policy 下のみ |
| Session | `branch` / `focus` | `focus_code` |
| Claim | `paths` / `reason` / `forced_over` | `path_count` / `reason_code` / `forced`（真偽） |
| Relay | `subject` / `body` | `code` |
| Conflict | `overlapping_paths` / `reason` | `overlap_count` / `reason_code` |

**allowlist が policy そのものである。** `*_FIELDS` / `*_SAFE_FIELDS` は各 view が
出してよいキーの一覧で、テストが「view のキー集合 == allowlist」を等号で確認する。
serializer にフィールドを足して判断を忘れると、漏れるのではなくテストが落ちる。

**repo slug は public identifier。** ADR-010 が envelope に対して置いた判断と同じで、
`AXONRELAY_SAFE_PUBLIC_IDENTIFIERS` のときだけ出す。

**拒否は行動可能なままにする。** claim を拒否されたとき、safe mode でも
「誰が持っているか（opaque ref）」と「どれだけ重なるか（件数）」は返る。相手には
relay で連絡でき、パスは自分が投げた入力なので返してもらう意味がない。

### 2. opaque identifier（migration 012）

`actors.opaque_id` と `workspaces.opaque_id`。既存行は backfill 済み、unique。

**派生ではなく乱数にした。** 置き換える対象（名前・パス）は推測可能なので、その
digest は opaque ではない。乱数なら、再起動をまたいで安定でありながら、値から
元の identity を復元できない。

### 3. 構造化コード

`focus_code` / `reason_code` / `code` / `ack_code` を Session / Claim / Relay /
RelayReceipt に追加し、MCP tool の引数として公開する。自由文の**開示可能な形**で
あり、safe mode の view はこれを出す。

migration は既存の自由文からコードを**推測しない**。文からコードを当てるのは、
この migration が裏付けられない主張になる。

> **範囲外（#31 へ）:** safe mode での調整レイヤーへの**書き込み**は ADR-010 の
> とおり拒否のままである。コードだけを送る producer 側の配線は #31（metadata-only
> lifecycle producer）の担当で、その issue が明示的に「Safe Envelope mode での
> structured focus and reason codes」を範囲に挙げている。本 ADR は、そこから来る
> コードを**受け取り、開示できる形にする**ところまでを決める。

### 4. モードに依存しない 2 つの規則

秘密の話であって開示の好みの話ではないので、full-text モードでも適用する。

- **agent config の値は serializer から出ない。** 出るのはキー名だけ
  （`config_keys`）。config は運用者が API キーを置く場所である
- **URL は保存時に検査する。** 認証情報付き、クエリ文字列、フラグメント、
  http/https 以外のスキームは拒否。`ExternalLink.url` の `@validates` に置いた。
  この列には**現在まだ書き手がいない**——だからこそモデル側に置いた。最初の書き手が
  規則を継承し、後から思い出す必要がない。拒否メッセージは URL を繰り返さない

### 5. 保持と削除（`app/retention.py`）

運用メタデータには寿命がある。**証跡には無い。**

| 対象 | 窓 |
|---|---|
| 終了した session | 30 日 |
| 解放済み / 期限切れの claim | 14 日 |
| 全員が ack した relay | 30 日 |
| 未 ack の relay | 180 日 |

sweep が触れてよいテーブルは `sessions` / `claims` / `relays` / `relay_receipts`
の 4 つだけ。`approvals` / `drafts` / `tasks` / `task_assignments` /
`external_links` / `actors` / `agent_definitions` / `safe_events` /
`credentials` / `workspaces` は対象外である。承認は判断対象の artifact を名指す
（ADR-009）ので、draft や task を消せば hash chain が検証不能になる——目的の正反対。
テストは、台帳を詰めた DB を sweep しても `verify_approval_chain` の結果が
1 バイトも変わらないことを確認する。また、**全テーブルが sweep 対象か保護対象かに
分類されていること**もテストが強制する。

窓は意図的に緩い。これは調整ボードであり、再接続しようとしている session を消すのは
1 週間長く持ちすぎるより悪い。

## 脅威モデル — safe mode で**なお観測できるもの**と、その理由

| 観測できるもの | なぜ残すか |
|---|---|
| **活動の時刻とリズム**（`started_at`、`last_heartbeat_at`、claim の作成/期限、relay の時刻） | 衝突検出そのものが時間の問題である。誰かが「いま」作業しているかを言わずにボードは機能しない |
| **相関の構造**（同じ `actor_ref` が同じ `workspace_ref` に繰り返し現れる、どの ref が誰に relay するか） | ref は安定でなければ claim と relay の履歴が連続しない。安定な ref は、長く観察すれば活動パターンを描く。**これは受け入れた交換である** |
| **作業の粗い性質**（`focus_code`、`reason_code`、`kind`、`code`） | 「待つべきか、別の場所を触るべきか」を決めるための最小情報。閉じた語彙なので、任意の文字列より観測できる量は小さい |
| **規模**（`path_count`、`overlap_count`、open relay の件数） | 重なりの大きさは判断材料であり、パスの中身は判断材料ではない |
| **repo slug**（public policy 下のみ） | 公開リポジトリの識別子は公開情報。private repo では policy を切ればよい |
| **REST の Actor 応答** | `/actors*` も同じ policy を通る。通していなければ、board が返す `actor_ref` の隣の `actor_id` を 1 回問い合わせるだけで名前に戻せてしまい、pseudonym が pseudonym でなくなる |
| **task / draft / approval の本文** | ADR-010 の safe mode は書き込みを拒否するので、safe mode の共有インスタンスはそもそも受け取らない。**モードを後から入れた場合、切替前のデータは残る**——その配備は切替時に既存データを扱う判断が要る |
| **DB とプロセスを持つ管理者に対して** | 何も隠さない。非目標（ADR-010 と同じ） |

## 移行と互換性

- migration `012` は列の追加と backfill のみ。既存の列は変更も削除もしない
- `AXONRELAY_SAFE_MODE` 未設定なら **レスポンスは従来と同一**。full-text の
  ローカル PoC ワークフローは何も変わらない（テストがそれを固定している）
- **既存配備への影響が 2 つある**（モードに依存しない規則）:
  1. `agent_definitions.config` の**値**が API / MCP のレスポンスから消える。
     キー名（`config_keys`）に置き換わる。値が要る運用は DB を直接読む
  2. `ExternalLink.url` に認証情報・クエリ・フラグメント・非 http(s) を渡すと
     拒否される。この行にはこれまで書き手がいなかったので、既存データへの影響はない
- `retention.sweep` は**自動では走らない**。`python -m app.retention --dry-run` で
  確認し、必要なら cron に置く。走らせなければ従来どおり何も消えない

## 代替案と却下理由

- **serializer ごとに条件分岐を書く**: 判断が 10 箇所に散り、新しいレスポンスを
  足した人が忘れる。allowlist + 等号テストなら忘れられない
- **safe mode で調整プレーンのレスポンス自体を止める**: ボードが機能しなくなる。
  最小化の目的は「使えなくする」ことではなく「必要な分だけ言う」ことである
- **opaque id を (host, repo, clone_path) の digest にする**: 安定で移行も要らないが、
  repo は公開されており path は推測可能なので、総当たりで元に戻せる
- **既存の自由文からコードを推測して backfill する**: 文からコードを当てた結果を
  不変フィールドに固定するのは、根拠のない主張を記録することである
