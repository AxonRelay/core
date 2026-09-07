# ADR-010: Safe Envelope — content-blind, metadata-only ingestion

- Status: Accepted (2026-09-07)
- Issue: #25（Epic #32 配信順序 2）
- 関連: [ADR-008](./adr-008-optional-bearer-token.md)、[ADR-009](./adr-009-artifact-commitment.md)、
  [schemas/safe-envelope-v1.json](./schemas/safe-envelope-v1.json)、migration `010`

## 文脈

task / relay / coordination の各 surface は任意の文字列を受け取る。個人 PoC では
それで良いが、共有インスタンスが**機密の成果物を受け取らない**ことを保証できない。
受信後の redaction では遅い——core が既にデータに触れている。

Epic #32 の製品境界は「AxonRelay は成果物を集めない。identity・coordination・
artifact commitment・decision についての最小限の evidence を記録する」である。

## 決定

### 1. 明示的な safe mode

`AXONRELAY_SAFE_MODE=1` で有効化する。既定は off で、既存の全文モード（個人 PoC）は
何も変わらない。フラグは呼び出し時に読む（`AXONRELAY_MCP_TOKEN` /
`AXONRELAY_TRUST_PROXY` と同じ流儀）。

safe mode では、自由文を受け取る surface はすべて**固定文言**で拒否する。
REST は 403、MCP は `ToolError`。文言は呼び出し元の値を一切補間しない。

| 拒否される surface（REST） | 拒否される tool（MCP） |
|---|---|
| `POST /tasks`, `PUT /tasks/{id}`, `POST /tasks/{id}/run`, `POST /tasks/{id}/approve`, `POST /tasks/{id}/reject`, `POST /agents`, `PUT /agents/{id}` | `create_task`, `run_task`, `approve_task`, `reject_task`, `review_pending_task`, `create_agent`, `update_agent`, `register_session`, `heartbeat_session`, `claim_territory`, `claim_git_resource`, `send_relay`, `ack_relay` |

ガードは各 surface の**最初の文**で、DB 書き込みや LangGraph 呼び出しより前に走る。
読み取り系（`get_task`, `get_board`, `read_inbox` など）は既存データをそのまま返す。
それらの表示は #26（metadata 最小化）の範囲。

### 2. Safe Envelope v1

唯一の書き込み経路は `POST /envelopes` と `ingest_safe_envelope`。両者は
`app/safe_envelope.py` の `ingest()` を呼ぶだけの薄いラッパで、`project_run_state`
と同じ「共有サービスに 1 実装」の規律に従う。

Envelope は Pydantic model（`extra="forbid"`）で、JSON Schema を
[docs/schemas/safe-envelope-v1.json](./schemas/safe-envelope-v1.json) に公開する。
テストが model と公開ファイルの一致を強制するので drift しない。

| フィールド | 形 |
|---|---|
| `schema_version` | リテラル `1` |
| `policy_version` | `^[A-Za-z0-9._-]{1,32}$` |
| `identifier_policy` | `opaque`（既定）/ `public` |
| `event_id`, `actor_id`, `workspace_id?`, `session_id?`, `artifact_id?` | opaque id: `^[A-Za-z0-9_-]{16,128}$`（`/` `.` `:` `@` を含めない = パス・URL・メール・slug が通らない） |
| `repository_id` | opaque id、または `public` policy 下で `owner/repo` slug |
| `action` | 閉集合 enum（`session_start` … `relay_ack`） |
| `outcome` | `success` / `refused` / `conflict` / `error` |
| `artifact_commitment?` | `^[0-9a-f]{64}$`、**producer が算出**。core は再計算しない |
| `artifact_commitment_algorithm?` | `sha256-utf8-v1`（ADR-009 と同じラベル） |
| `artifact_version?` | 整数 ≥ 1 |
| `occurred_at` | timezone-aware datetime |
| `producer_signature?` | base64url、16〜1024 文字。保存・返却のみで検証はしない |

`title` / `description` / `prompt` / `draft` / `feedback` / `body` / `subject` /
`url` / `clone_path` / `host` … を入れる**場所が無い**。未知フィールドは拒否。

保存先 `safe_events` テーブルには **Text 列が存在しない**（テストで構造的に保証）。
`event_id` は unique で、再送は冪等（既存行を返し `created=false`）。

### 3. 拒否は値を含まない

- Pydantic の `ValidationError` は入力値を保持するので、サービス内で消費して
  **フィールド名のリスト**に変換し、元の例外は再送出しない（`raise ... from None`）
- REST: `422 {"detail": {"reason": ..., "fields": [...]}}`
- MCP: `ToolError` にフィールド名のみ。SDK は入力スキーマ違反を
  `str(ValidationError)`（`input_value` を含む）で返すため、tool の引数は
  `dict[str, Any]` で受けて**サービス側で**検証する
- ログ: `axonrelay.safe_envelope` logger はフィールド名と enum 値だけを出す
- **FastAPI 既定の 422 ハンドラを差し替え**、全エンドポイントで `input` / `ctx` を
  返さない（`loc` と `type` のみ）。これは safe mode に関係なく常時有効

### 4. public identifier は policy 下でのみ

`identifier_policy="public"` の envelope は `AXONRELAY_SAFE_PUBLIC_IDENTIFIERS=1`
のときだけ受理する。それ以外は opaque id が必須。

## この決定が保証するもの・しないもの

| 性質 | |
|---|---|
| safe mode で core が成果物本文を受け取らない | **する**——受け取る場所が無い |
| 拒否された値が DB・ログ・エラー・レスポンス・外部呼び出しに現れない | **する**（canary テスト） |
| envelope の commitment が本物の成果物のものである | **しない**——producer を信頼する。署名検証は将来 |
| 同一プロセス・同一 DB の管理者からのデータ隠蔽 | しない（非目標） |
| safe mode での coordination board への書き込み | **まだ**。lifecycle producer（#31）と metadata 最小化（#26）が envelope 経由の書き込みを担う |
| 汎用 DLP、LLM による自動分類 | しない（非目標） |

## 残る露出

- MCP SDK の入力スキーマ違反（例: `envelope` に文字列を渡す）は SDK が
  `input_value` 付きで返す。トップレベルの型違反であり、フィールド内容ではないが、
  文書化しておく
- uvicorn の access log はクエリ文字列を含む（`GET /coordination/git/guard?clone_path=...`）。
  #26 の範囲

## 代替案と却下理由

- **受信後に正規表現で redaction**: core が既に触れている。Epic の非目標
- **既存 tool の引数を safe mode で「無視」する**: 無視は受信であり、SDK のログや
  トレースに残り得る。拒否して受け取らない
- **safe mode で既存 surface を metadata に自動変換**: 変換元の自由文を受け取る時点で失格
