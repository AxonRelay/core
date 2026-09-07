# ADR-011: 呼び出し元 identity と scope 付き認可

- Status: Accepted (2026-09-07)
- Issue: #27（Epic #32 配信順序 3）
- 関連: [ADR-008](./adr-008-optional-bearer-token.md)、[ADR-009](./adr-009-artifact-commitment.md)、
  [ADR-010](./adr-010-safe-envelope.md)、[SECURITY.md](../SECURITY.md)、migration `011`

## 文脈

ネットワーク隔離（Tailscale / Tunnel）と ADR-008 の任意共有トークンは
**誰がポートに到達できるか**を制限する。どちらも

- **どの呼び出し元がその操作を行ったのか**
- **その呼び出し元は何をしてよいのか**

には答えない。共有トークンが言えるのは「秘密を持つ誰か」までで、その誰かは特定
できない。台帳の価値が「誰が何を承認したか」である以上、これは中心的な欠落である。

さらに、Actor 名は呼び出し元が決めていた。`register_session(actor_name=...)` は
渡された名前をそのまま採用し、承認の reviewer は「最初に見つかった human Actor」
だった。REST も MCP も、呼び出し元が自分を誰と名乗るかを検証していなかった。

## 決定

### 1. credential が Actor を名指す

`credentials` テーブル（migration 011）の 1 行が、token を Actor と scope 集合に
結びつける。**保存するのは token の SHA-256 だけ**で、token 自体は
`python -m app.credentials issue` の出力に一度だけ現れる。照合は digest による
単一インデックス参照なので、平文はクエリにもログにも現れない。比較は
`hmac.compare_digest`。

`actor_id` は `ON DELETE CASCADE` の実 FK である。台帳フィールド（ADR-009）が
FK を避けるのと**逆の判断**で、意図的にそうしている: これは証跡ではなくアクセス
制御であり、削除された identity が認証し続けてはならない。

### 2. 記録される Actor はサーバが決める

認証済みの呼び出しでは、承認の reviewer、task の creator、session の Actor は
credential の Actor である。リクエストパラメータで別の Actor を名乗る呼び出しは
**無視ではなく拒否**する（`authz.check_claimed_actor`）。無視は「その Actor として
成功した」と信じさせるので、拒否のほうが安全である。

`GET /actors/me` と `get_self_actor` は「サーバから見たあなた」を返す。
（副次的に、`/actors/me` が `/actors/{actor_id}` より後に宣言されていて到達不能
だった既存の不具合もここで直した。）

**session id も identity の主張である。** 調整レイヤーの surface はすべて session を
id で指す。id はリクエストパラメータなので、そのままでは `coordination:write` の
credential が他人の agent として relay を送り、territory を解放し、session を終了
できてしまう。`coordination:read` でも受信箱の読み取りは RelayReceipt を書くので、
他人の未読を既読にできる。よって **session id を受け取る surface はすべて、その
session が credential の Actor のものであることを確認する**（`check_session_owner`）。
loopback（オペレータ自身のプロセス）はマシン上の全 session を正当に駆動するので
対象外。

**`ledger:write` は「承認してよい」を意味しない。** 同じ scope が task の作成・実行と
承認を覆うため、そのままでは agent の credential が自分で作った draft を承認し、
reviewer として記録され得る——台帳が可視化するはずの当のものが崩れる。よって
credential による承認は、**その task で `approver` assignment を持つ Actor**、または
**human Actor** に限る（`check_may_approve`）。データモデルは誰が承認者かをすでに
表現していたが、それを強制するものが無かった。loopback は従来どおり。

### 3. scope は 6 つ、surface は必ずどれかに割り当てる

| scope | 何を許すか |
|---|---|
| `ledger:read` | task / draft / approval / agent の読み取りと台帳検証 |
| `ledger:write` | task 作成・実行・承認・差戻し、agent 定義の作成と更新 |
| `coordination:read` | ボード、claim、inbox、guard 照会、取り込み済み envelope |
| `coordination:write` | session / claim / relay の書き込み、Safe Envelope の取り込み |
| `export:read` | **予約**。公開安全な集計エクスポータ（#28）が使う。現時点でどの surface にも割り当てていない |
| `administration` | 破壊的操作（agent / task / assignment の削除）。Actor 削除は旧承認行の hash 済み reviewer 参照を NULL にし得るので、これは台帳に影響する権限である |

scope は**必要条件であって十分条件ではない**。上の 2 つの追加検査（session の所有と
承認者の資格）は scope を通過した後に走る。

MCP は 30 の tool を個別に書き換えるのではなく、SDK の `ServerMiddleware`
（`(ctx, call_next)` で全 inbound メッセージを包む）を 1 つ入れる。REST は
FastAPI のアプリ全体依存を 1 つ入れる。**判断は 1 箇所**で、`authz.TOOL_SCOPES` /
`authz.RESOURCE_SCOPES` / `authz.ROUTE_SCOPES` に載っていない surface があると
テストが落ちる。既定が「開いている」にならないための構造的な保証である。

> SDK の `Server.middleware` は upstream で provisional（2.x マイナーで署名が
> 変わり得る）と明記されている。`test_authz.py` がその contract
> （`ServerRequestContext` のフィールドと `ServerMiddleware.__call__` の署名）を
> 固定しているので、変更は本番ではなくテストで落ちる。

### 3.5. ADR-008 の共有トークンと同じヘッダを共存させる

両方を設定した配備では `Authorization: Bearer` に入り得る値が 2 種類ある。
共有トークンのゲート（`app/mcp/http_auth.py`）は **どちらかに一致すれば通す**:
共有シークレットは「ノックしてよい」の粗いゲート、credential は identity であり、
その後ろの認可ミドルウェアが「何をしてよいか」を決める。片方しか通さない実装だと
2 層が排他になり、ADR-011 が推奨する「重ねる」構成が動かない。

### 4. 強制は opt-in、stdio は常に loopback

`AXONRELAY_REQUIRE_AUTH=1` のときだけ HTTP 呼び出し元に credential を要求する。
未設定なら挙動は従来と完全に同じで、すべての呼び出しは **loopback principal**
（オペレータの human Actor・全 scope）として走る。ADR-008 が置いた
「未設定なら何も変わらない任意の層」と同じ形である。

**stdio は強制下でも loopback である。** stdio transport には HTTP リクエストも
ヘッダも無く、呼び出し元はオペレータが自分のマシンで起動したプロセスである。
これは見落としではなく信頼の仮定であり、ここに記録する。`ctx.request is None`
がその判定で、ヘッダの有無ではない。

### 5. 拒否は何も明かさない

- 401: 必要なのが credential であること以外を言わない（`WWW-Authenticate: Bearer`）
- 403: 必要な **scope 名**だけを返す。リソースの存在有無は含まない——scope 検査は
  リソース参照より前に走るので、存在しない task と読めない task の応答は同一になる
- token と `Authorization` ヘッダは保存・エコー・ログのいずれにも現れない（canary テスト）

**明示的に開けてあるもの**: ヘルスチェック（`GET /`）と FastAPI のスキーマ経路
（`/openapi.json` / `/docs` / `/redoc`）。前者は秘密を要求する liveness probe が
probe として機能しないため。後者は API の**形**を述べるだけで、この形は公開
リポジトリにそのまま載っている。これらは Starlette route であってアプリ全体依存の
対象外なので、`PUBLIC_ROUTES` に名前を書き、構造テストが route 表全体を見ることで、
「開いている」を事故ではなく判断にしている。

CORS の `allow_headers` に `Authorization` を含める。含めないとブラウザの preflight
が失敗し、強制下でダッシュボードから credential を提示する経路が無くなる。

## この決定が保証するもの・しないもの

| 性質 | |
|---|---|
| 強制下で、非 stdio の全呼び出しが認証済み identity を持つ | **する** |
| 記録される Actor がリクエストパラメータで変えられない | **する** |
| MCP と REST が同じ scope 表・同じ判定を使う | **する**（対応をテストで固定） |
| token が保存・エコー・ログに出ない | **する** |
| stdio 呼び出し元の identity 検証 | **しない**。loopback 信頼（上記 4） |
| 同一プロセス・同一 DB の管理者に対する防御 | しない（非目標） |
| OAuth 2.1 / 汎用 identity provider / マルチテナント | しない（非目標。ADR-008 の理由がそのまま生きる） |
| 他人の session を駆動できないこと | **する**（`check_session_owner`） |
| agent が自分の draft を承認して reviewer として記録されないこと | **する**（`check_may_approve`。`approver` assignment か human Actor が必要） |
| ブラウザからのダッシュボード利用 | CORS は通るようにしたが、credential の保管と提示はダッシュボード側の未実装分。強制下での本番利用はリバースプロキシ（Cloudflare Access 等）を前提にする |

## 代替案と却下理由

- **各 tool / endpoint に検査を書く**: 30 + 31 箇所。新しい surface を追加した人が
  忘れれば既定は「開いている」。1 箇所 + 構造テストなら忘れようがない
- **共有トークン（ADR-008）を per-caller に拡張する**: 「秘密を持つ誰か」から
  「どの誰か」へは、token を Actor に結びつける行が要る。それが credential である。
  ADR-008 の層は残る——到達性の粗いゲートとして、引き続き有効
- **OAuth 2.1 を採用する**: ADR-008 が述べた理由（単一オペレータ配備に issuer と
  resource server metadata の正直な値が無い）は変わらない。将来 OAuth に移るときは
  この層が `token_verifier` に置き換わり、`authz` の scope 表はそのまま使える
- **`export:read` を今は宣言しない**: #28 が来たときに credential の再発行が要る。
  issue が「initial scopes」として 6 つ挙げている以上、今宣言して予約しておくほうが
  運用が素直である（未割り当てであることをテストで明示している）
