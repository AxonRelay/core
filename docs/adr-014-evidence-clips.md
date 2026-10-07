# ADR-014: Evidence Clip — 共有台帳は digest と出所だけを持つ

- Status: Accepted (2026-10-07)
- Issue: #52（PoC）、PR #53 の載せ直し。Epic #32 の content-blind 方針に従う
- 関連: [ADR-009](./adr-009-artifact-commitment.md)、[ADR-010](./adr-010-safe-envelope.md)、
  [ADR-011](./adr-011-caller-identity.md)、[ADR-012](./adr-012-metadata-minimization.md)、
  [ADR-013](./adr-013-mcp-2026-interaction.md)、migration `014`

## 文脈

PR #53 は Evidence Clip を実装した。運用者が明示的に選んだ抜粋を task に結び付け、
draft から `[E-n]` で引用し、Context Pack としてエージェントに渡す。ただし #53 は
P0 スタック（#24〜#27、#30）より前から分岐しており、次の 2 点が main と両立しなかった。

1. **承認の束縛と配送を独自に実装していた。** `approved_content` / `approved_draft_sha256` /
   `decision_key` / `delivery_status` は ADR-009 と ADR-013 と重なり、`decision_key` は
   意味まで違っていた
2. **抜粋の本文（`quote`）、ページ title、モデルの注釈を共有台帳に保存していた。** これは
   Epic #32 の「AxonRelay は成果物を集めない」に反する

2026-10-06 の判断（PR #53 のコメント）: P0 スタックの後に migration `014` として載せ直す。
承認・`decision_key`・配送は main の実装に統一し、共有台帳は **sha256 と出所だけ**を持つ。
本文はローカル側に置き、Context Pack はローカルで解決する。

## 決定

### 1. 共有台帳の行には本文の置き場が無い

`evidence_clips` の列は次で全部である。テストが列集合を等号で確かめ、Text 型の列が
無いことも確かめる。

| 列 | 中身 |
|---|---|
| `id` | `E-<id>` が引用形 |
| `task_id` | 1 つの task に属する。task とともに消える |
| `captured_by_actor_id` | 呼び出し元の Actor（ADR-011）。リクエストの値ではない |
| `source_url` | http(s)、userinfo・query・fragment なし（下記） |
| `source_type` | `public` / `personal` |
| `content_sha256` / `content_algorithm` | 抜粋の UTF-8 バイト列の SHA-256、`sha256-utf8-v1`（ADR-009 と同じラベル） |
| `captured_at` | サーバ時刻 |

`evidence_feedback` は `relevant` / `irrelevant` / `misleading` の verdict と Actor・時刻
だけを持ち、コメント欄は無い。

REST の request model は `extra="forbid"` で、`quote` / `title` / `annotations` を送った
クライアントは 422 を受け取る。黙って捨てるのではなく拒否するのは、送り手が
「本文はローカルに残る」ことを知る必要があるからである。422 は値を返さない（ADR-010）。

### 2. URL は ExternalLink と同じ規則

`source_url` は `disclosure.sanitize_url` を通る（ADR-012 と同じ `@validates`）。
#53 は既知のキーだけを `[REDACTED]` に置き換えていたので、`X-Amz-Signature` や `sig`
を持つ presigned URL が生きた資格情報ごと保存された（#53 のレビュー指摘 3）。
キーの一覧を育てるのではなく、query と fragment を丸ごと拒否する。クライアントは
送る前に取り除き、完全な URL は手元の記録に残す。

### 3. 本文はローカルの content-addressed store に置く

`app/evidence_local.py` が `$AXONRELAY_EVIDENCE_DIR/<sha256>.json`
（既定 `~/.axonrelay/evidence`、ファイルは 0600）に本文・title・完全な URL・注釈を
置く。ファイル名が digest で、**読むたびに本文を再計算して照合する**。合わない記録は
返さない——台帳の commitment の下に別の文面を見せることになるから。

入口は 2 つ:

- `python -m app.evidence_local put --title T --url U < excerpt.txt` が digest を出力し、
  その digest を `capture_evidence_clip` / `POST /tasks/{id}/evidence-clips` に渡す
- Chrome 拡張（`experiments/chrome-evidence-clip`）はブラウザで SHA-256 を計算して
  digest と URL だけを送り、本文はプロファイル内に残す。JSONL で書き出したものを
  `python -m app.evidence_local import` が取り込む。行の digest と本文が合わなければ拒否

### 4. Context Pack はローカルで組み立てる

`get_context_pack` はサーバから manifest（ref・digest・出所）を取り、本文は
**MCP サーバのプロセスが動いているマシンの store から**読む。

- **stdio**: サーバはオペレータ自身のプロセスで、本文を持つマシンの上にある。
  store から解決し、#53 と同じ決定的な trigram ランキング（モデル呼び出しなし、
  原文・source claim・解釈の 3 平面に重み）で並べる
- **Streamable HTTP**: サーバは共有インスタンスでありうる。そのディスクは呼び出し元の
  store ではないので、**読まない**。全 clip を `unresolved` として digest と出所だけ返す

`unresolved`（ref・digest・出所）は「一致しなかった」と「このマシンに無い」を区別するために返す。
予算はまず `items` に使い、`unresolved` は残りに入る分だけ載せて `unresolved_count` で全件数を
示す。解決できたが予算に収まらなかった一致は `omitted_count` に数える——clip が痕跡なく消える
ことはない。同じ本文を複数のページから取った場合、ローカルの記録はページごとの capture
（title・完全な URL・注釈）を持ち、pack は clip の台帳 URL に対応する capture だけを見せる。
pack は `char_budget`（2,000〜20,000 文字）を超えない。先頭の 1 件が収まらなければ
注釈・title を落とし、本文を接頭辞に切って `verbatim_complete: false` を付ける。
ref・digest・URL は落とさない——それが無いと引用も照合もできない。

REST には Context Pack の endpoint を置かない。サーバ側では本文を解決できないので、
置いても manifest と同じものしか返せない。manifest は `GET /tasks/{id}/evidence-clips`。

### 5. 承認には手を入れない

#53 の承認エピソード・`approved_content`・`evidence_manifest`・配送状態機械は持ち込まない。
承認・`decision_key`・配送は ADR-009 / ADR-013 の実装のままである。

承認と clip の結び付きは**既にある鎖**で足りる。承認は draft の版と commitment に束縛され
（ADR-009）、draft の本文は `[E-n]` を含み、`E-n` は不変の行で digest を持つ。引用を
承認の hash payload に別途入れる（v4）ことはしない。`validate_evidence_references` は
draft の引用がこの task の clip を指すかを確かめる読み取り専用の tool で、承認の前提条件
にはしない。

### 6. surface の分類

| surface | scope（ADR-011） | safe mode（ADR-010） |
|---|---|---|
| `POST /tasks/{id}/evidence-clips`、`capture_evidence_clip` | `ledger:write` | 拒否（URL は呼び出し元の文字列） |
| `GET /tasks/{id}/evidence-clips`、`list_evidence_clips` | `ledger:read` | 読み取り |
| `get_context_pack` | `ledger:read` | 拒否（query を受けて返す。`check_conflicts` と同じ扱い） |
| `validate_evidence_references` | `ledger:read` | 拒否（draft の本文を受ける） |
| `POST /evidence-clips/{id}/feedback`、`evaluate_evidence_clip` | `ledger:write` | 許可（id と閉じた enum だけ） |

保持（ADR-012）: `evidence_clips` / `evidence_feedback` は sweep の対象外。承認済み draft が
引用している clip を消すと、引用先の無い承認が残る。

## この決定が保証するもの・しないもの

| 性質 | |
|---|---|
| 共有台帳が抜粋の本文・title・注釈を持たないこと | **する**（列が無い。テストで構造的に確認） |
| 台帳の URL が query / fragment / 資格情報を持たないこと | **する** |
| ローカルで表示する本文が台帳の digest と一致すること | **する**（読むたびに照合） |
| URL の path 自体が機微であること（`/users/<name>/...`） | **しない**。path は出所そのものなので残す。safe mode では capture を拒否する |
| 短い・推測しやすい抜粋の digest からの総当たり復元 | **しない**。digest は秘匿ではなく commitment である。秘匿が要る抜粋は台帳に載せない |
| 他の人の store にある本文を自分の pack に出すこと | **しない**（非目標）。共有するのは commitment であって本文ではない |

## 代替案と却下理由

- **#53 をそのまま rebase する**: 承認の二重実装と本文の保存が残る。判断で退けた
- **本文を暗号化して台帳に置く**: 鍵の配布という別の問題を持ち込み、content-blind の
  主張が「鍵を持たない者には」に弱まる
- **サーバ側で本文を検索する Context Pack**: サーバが本文を持たない以上、成り立たない
- **query string の既知キーだけを redaction する**: #53 の方式。キーの一覧は常に足りない
- **引用を承認の hash payload に入れる（v4）**: draft の commitment が既に引用を束縛して
  おり、clip は不変。追加の版は検証の分岐を増やすだけで、保証は増えない
