# ADR-009: 承認を不変の artifact commitment に束縛する

- Status: Accepted (2026-09-07)
- Issue: #24（Epic #32 配信順序 2）
- 関連: [ADR-006](./adr-006-no-message-broker.md)、[regulatory-positioning.md](./regulatory-positioning.md)、migration `009`

## 文脈

migration 004 の hash chain は承認**イベント**の改ざんを検知するが、その承認が
**どの draft** に対するものかを記録していなかった。`Approval` は `task_id` と
`comment` を持つだけで、`Draft` への参照が無い。したがって

- draft が差し替わっても承認は暗号学的に有効なまま残る（束縛の曖昧さ）
- MCP の `review_pending_task` だけが「表示した本文と一致するか」を文字列比較で
  守っており、REST と `approve_task` / `reject_task` には同等の防御が無い
- 修正 draft（`modified_draft`）は graph 往復の**後**に `Draft` 行になるため、
  承認時点では承認対象の版が存在しない

という状態だった。

## 決定

### 1. Draft は commitment を持つ artifact である

`Draft` に `commitment`（本文 UTF-8 バイト列の SHA-256）、`commitment_algorithm`
（`sha256-utf8-v1`）、`producer_actor_id` を追加し、`(task_id, version)` を
unique にする。commitment は全文モードでは AxonRelay が算出するが、
`add_draft(commitment=...)` で producer 側の値を渡すこともでき、その場合は
本文と一致しなければ拒否する（#25 の metadata-only モードでは本文を受け取らず
commitment だけを受け取る前提）。

### 2. Approval は artifact binding を hash に含める

`Approval` に `artifact_ref`（`axonrelay://tasks/{task_id}/drafts/{version}`、
MCP resource URI と同じ形）、`artifact_version`、`artifact_commitment`、
`artifact_commitment_algorithm`、`producer_actor_id` を追加し、**5 つとも**
entry hash の入力に含める。hash payload には `"v": 2` を入れ、行には
`hash_version = 2` を記録する。

### 3. 承認には必ず対象 artifact がある

`record_approval` は task 行ロックの下で

1. 最新 draft を取る。無ければ `ArtifactRequiredError`
2. 呼び出し元が `artifact_version` / `expected_commitment` を渡していれば最新
   draft と照合し、違えば `StaleArtifactError`（何も記録しない）
3. `modified_draft` があれば**先に**新版を追加する（producer = reviewer）。
   本文が同じなら版は増えない
4. 対象版の binding を含めて hash を計算し、承認を記録する

REST は stale / 対象なしを **409**、MCP は `status="stale_decision"` で返す。
`review_pending_task` は表示した版と commitment をそのまま束縛対象として渡す
ので、文字列比較ではなくロック下の commitment 照合で staleness を防ぐ
（#30 の staleness check の土台）。

### 4. 旧行は書き換えない

- 004〜008 の行は migration で `hash_version = 1` を刻み（NULL は「一度も hash されていない」
  行だけの意味になる）、v1 payload で検証し、`artifact_bound = false` として返す。
  改ざんではなく「束縛なし」と報告する。検証は行自身の版で payload を選ぶ
  （`hash_version >= 2` で binding あり）ので、将来 v3 を導入しても v2 行が
  改ざん扱いにならない
- 既存 draft の commitment は migration で backfill する（本文があるので決定的）。
  既存 approval への binding は backfill **しない**——事後の束縛は推測になる
- `verify_task_ledger` は `artifact_bound` / `unbound` の件数を追加で返す

### 5. producer_actor_id は FK にしない

`reviewer_actor_id` は `ON DELETE SET NULL` の FK で、Actor 削除で hash 対象の
値が変わり chain が壊れる（既存の設計上の弱点）。新しい台帳フィールドは
その轍を踏まないよう素の整数にする。reviewer 側の是正は別 issue。

## この決定が保証するもの・しないもの

| 性質 | 保証 |
|---|---|
| 承認イベントの改ざん検知 | する（004 から） |
| 承認が**どのバイト列**に対するものかの特定 | する（commitment で） |
| 保存されている draft 本文が承認時と同一であること | しない。`Draft.commitment` と本文の再計算を比較する**別のチェック**。台帳は本文を hash していない |
| 外部 artifact の内容の保管 | しない（非目標） |
| 適格電子署名・タイムスタンプ局・否認防止 | しない（[regulatory-positioning.md](./regulatory-positioning.md)） |

## 代替案と却下理由

- **Approval → Draft の FK だけ追加する**: 参照は取れるが hash に入らず、
  FK の SET NULL / CASCADE で台帳値が動く。値そのものを行に複製して hash する
  方が「不変」の意味に合う
- **hash payload をそのまま拡張する（版番号なし）**: 検証が現行の関数で再計算
  するため既存行が全て `valid=false` になる。payload 内の `"v"` と行の
  `hash_version` で旧行を旧 payload として検証する
- **既存 approval に最新 draft を遡及束縛する**: 当時どの版を見たかは分からない。
  推測を不変フィールドに固定するのは目的に反する

### 6. 既知の挙動: resume 失敗後の再試行

承認は graph の resume **より前**に台帳へ書かれる（artifact が先、承認が後、
その後に graph）。resume が失敗（Platform 未設定で 503 など）しても台帳の
エントリと（`modified_draft` があれば）新版はすでに確定している。同じ
`artifact_version` で再送すると、自分の前回の試行が版を進めているため 409 /
`stale_decision` になる。これは仕様で、台帳は「判断があった」事実を捨てない。
再試行は `get_drafts` で最新版を読み直してから行う。旧実装は再試行で承認行が
二重に記録されていた。

投影（`project_run_state`）は版番号だけでなく本文でも一致判定するので、
台帳が graph より 1 版先行した状態から graph が次の draft を返しても、
その draft は次の版として追加され、表示と束縛がずれない。

## 影響

- migration `009`（columns / unique / index / draft backfill）。ダウングレードは
  可能だが、v2 行が書かれた後の downgrade は復元ではなくバックアップ戻しにする
- REST: `ApproveRequest` / `RejectRequest` に `artifact_version` /
  `expected_commitment`（任意）、`ApprovalResponse` / `DraftResponse` に binding
- MCP: `approve_task` / `reject_task` に同じ引数、戻り値に `approval` を同梱
- テスト: SQLite（正常 / 各フィールド改ざん / 版番号ダウングレード / 旧行 / stale）、
  Postgres（migration 適用、`(task_id, version)` unique、8 並行 editor、
  008 時点のデータを積んだ DB への 009 適用）
