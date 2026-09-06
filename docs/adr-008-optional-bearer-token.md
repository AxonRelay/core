# ADR-008: Streamable HTTP transport に任意の共有シークレットを置く

> 決定: 2026-09-06 / Status: 採用 / 関連: [coordination-spec.md §8, §10](./coordination-spec.md), [SECURITY.md](../SECURITY.md), `backend/app/mcp/http_auth.py`

---

## 文脈

MCP サーバの Streamable HTTP transport（`--http`）には呼び出し元認証がない。これは
[coordination-spec.md §10](./coordination-spec.md) が「個人 PoC。境界はネットワーク層
（Tailscale / Tunnel）で引く」として意図的に対象外にしたものであり、ピボット
（[delta-mvp-spec.md](./delta-mvp-spec.md)）が認証を標準へ委譲した帰結でもある。

リポジトリを公開したことで、この線引きの意味が変わった。設計は公開情報になり、
「ポートに到達できれば台帳を読めて承認できる」ことを誰でも知っている。到達性の
境界（tailnet の外に出ない、`0.0.0.0` にしない）が一度でも破れた瞬間に、全権が
渡る。守りが**一段しかない**状態である。

## 決定

**`AXONRELAY_MCP_TOKEN` が設定されているときだけ、HTTP transport の全リクエストに
`Authorization: Bearer <token>` を要求する。未設定なら挙動は従来と完全に同じ。**

実装は SDK の `streamable_http_app()` を包む 30 行ほどの ASGI ミドルウェア
（`app/mcp/http_auth.py`）。トークンは定数時間比較、失敗は MCP 層に届く前に
`401` + `WWW-Authenticate: Bearer` で返す。stdio transport には関係しない。

クライアント側は `claude mcp add -t http -H "Authorization: Bearer <token>" ...` の
ように固定ヘッダを付けるだけでよい。

## 理由

1. **ネットワーク境界を置き換えるのではなく、重ねる。** Tailscale の内側に留める
   規則は変わらない。トークンは、その規則が破れたときの二段目である。
2. **未設定なら何も変わらない。** 既存の運用・テスト・ドキュメントの前提を壊さない。
   これは「任意機能」であって「認証の導入」ではない。
3. **標準への委譲と矛盾しない。** MCP の HTTP 認可は OAuth 2.1 で、SDK には
   `token_verifier` / `AuthSettings` の受け口がある。しかし `AuthSettings` は
   issuer と resource server のメタデータ URL を必須にしており、単一オペレータの
   配備には正直な値がない。偽の issuer を書いて SDK の経路に乗せるより、共有
   シークレットを共有シークレットとして実装するほうが誠実である。OAuth を本当に
   使う日が来たら、このミドルウェアを外して `token_verifier` に置き換えるだけで、
   呼び出し側の変更は「ヘッダの出どころ」だけになる。

## 対象外

- **REST API（`:8000`）には掛けない。** ダッシュボードはブラウザから直接叩くので
  固定ヘッダを注入できず、掛けるならリバースプロキシ側（Cloudflare Access 等）の
  仕事になる。`gitsafe` の guard 照会も同じ API を使う。REST はネットワーク境界の
  一段のままとし、それを [SECURITY.md](../SECURITY.md) に明記する。
- **トークンのローテーション・複数トークン・失効** — 個人 PoC には過剰。
  必要になったらそれは OAuth に移る合図である。

## 影響

- `deploy/DEPLOYMENT.md` §3.2–3.3 と `docs/mcp-server.md` に設定手順を追加
- `SECURITY.md` の「守らないもの」の記述を、任意トークンを含む形に更新
- [coordination-spec.md §10](./coordination-spec.md) の「MCP transport の呼び出し元認証」
  行は、本 ADR を参照する
