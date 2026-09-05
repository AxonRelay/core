# デプロイ手順 — ホスティング切替と調整ボードの稼働

日本語 | **[English](DEPLOYMENT.md)**

目標構成（固定費ゼロ・ホストに公開 IP なし）:

```
IDE ─ MCP（stdio / Tailscale 経由の Streamable HTTP）─▶ AxonRelay backend（自宅 PC / 小規模 VPS）
Browser ─▶ app.axonrelay.com（Vercel / Cloudflare Pages・静的ダッシュボード）
            └─ fetch ─▶ api.axonrelay.com（Cloudflare Tunnel ─▶ backend:8000）
LangGraph Platform（LangSmith Deployment）がエージェント graph を実行。
```

> **このリポジトリの範囲。** backend / graph / MCP サーバ / ダッシュボード、および
> 下記の tunnel テンプレートまで。アカウント側の操作（Cloudflare 認証、Tailscale、
> Vercel、AWS EC2 の解約、DNS）は **オペレータ作業**で、自動化されていません。
> あなたが実行する手順に **[you]** を付けています。

---

## どこから手を付けるか

やりたいことによって必要な範囲が変わります。**全部やる必要はありません。**

| やりたいこと | 必要な章 |
|---|---|
| 1台のマシンで試す（IDE から MCP を使うだけ） | §1 のみ（+ stdio 起動） |
| **複数デバイス・複数 clone で調整ボードを使う** | §1 → §3 |
| ダッシュボードを外から見る / API を公開する | §1 → §2 → §4 |
| 旧 EC2 構成から完全に移行する | §1 → §2 → §4 → §5 |

調整レイヤー（Phase 3）が目的なら **§2・§4・§5 は不要**です。§1 と §3 だけで動きます。
Cloudflare Tunnel はダッシュボードと公開 API のためのもので、MCP の経路ではありません。

---

## 1. Backend を動かす

自宅 PC か小規模 VPS で:

```bash
cp .env.example .env          # DATABASE_URL / LANGGRAPH_* / ANTHROPIC_API_KEY を記入
docker compose up -d postgres backend
docker compose exec backend alembic upgrade head   # 001..008 を適用
```

**`alembic upgrade head` は必ず `008` まで到達させること。**
migration 002 と 007 は「新規 Postgres にチェーンが適用できない」問題と
「enum ラベルの大小不一致で Actor の insert が全て失敗する」問題を直しています。
007 に届いていないと `register_session` が失敗し、調整ボードに参加できません。008 は git 資源 claim（§3.6）用の列を追加します。

確認:

```bash
docker compose exec backend alembic current    # -> 008 (head)
curl -s http://localhost:8000/ | head -c 200   # -> ヘルスチェック JSON
```

<details>
<summary>うまくいかないとき</summary>

- **`type "actortypeenum" already exists` で止まる** — 007 より前の古いコードで
  マイグレーションを回している。`git pull` してから再実行。
- **`invalid input value for enum actortypeenum: "HUMAN"`** — 007 が未適用。
  `alembic current` を確認して `alembic upgrade head`。
- **DB を作り直したい**（個人 PoC なのでデータに価値がない場合）:
  `docker compose down -v && docker compose up -d postgres` してから再度 upgrade。

</details>

---

## 2. Cloudflare Tunnel → api.axonrelay.com

> 調整ボードだけが目的ならこの章は飛ばしてよい。

**[you]** 認証と tunnel 作成（初回のみ）:

```bash
cloudflared tunnel login
cloudflared tunnel create axonrelay
cloudflared tunnel route dns axonrelay api.axonrelay.com
```

そのうえで、どちらか:

- **設定ファイル方式:** [`cloudflared.example.yml`](cloudflared.example.yml) を
  `~/.cloudflared/config.yml` にコピーし、tunnel id を埋めて
  `cloudflared tunnel run axonrelay`
- **トークン方式（サイドカー）:** tunnel トークンを `.env` に `TUNNEL_TOKEN` として置き、

  ```bash
  docker compose -f docker-compose.yml -f deploy/docker-compose.tunnel.yml up -d
  ```

TLS は Cloudflare 側で終端され、`backend:8000` に転送されます。ホスト側に
インバウンドポートを開ける必要はありません。

---

## 3. Tailscale と共有 MCP エンドポイント（調整ボードに必須）

調整ボード（Phase 3）は、**すべてのデバイスとすべての clone が同じ AxonRelay
インスタンスを見ている**ことで初めて意味を持ちます。ラップトップごとに Postgres を
立てると、ボードが分断されて衝突検出がまったく成立しません。

stdio はクライアント1つにつきサーバプロセス1つなのでこれができません。
リモートのデバイスは Streamable HTTP を使います。

### 3.1 Tailscale に参加する

**[you]** ホストと、コードを書くすべてのデバイスを同じ tailnet に join する。

```bash
tailscale status          # ホスト名（<host>.<tailnet>.ts.net）を控える
```

### 3.2 ホストで MCP サーバを起動する

```bash
cd backend
DATABASE_URL="postgresql://axonrelay:axonrelay_dev@localhost:5432/axonrelay" \
  python -m app.mcp.server --http --host 0.0.0.0 --port 8765
```

> **`DATABASE_URL` がコンテナ内と違う点に注意。** compose 内の backend は
> `@postgres:5432` を使いますが、これは Docker ネットワーク内のホスト名です。
> MCP サーバをホスト側で直接動かす場合は、compose が公開している
> `@localhost:5432` を指す必要があります。ここを間違えると
> `could not translate host name "postgres"` で起動に失敗します。

> ⚠️ **`--host 0.0.0.0` が安全なのは Tailscale の内側にいる場合だけです。**
> このトランスポートには**呼び出し元認証がありません** — ポートに到達できる人は
> 誰でも台帳を読め、タスクを承認できます。tailnet にバインドするか、既定の
> `127.0.0.1` のままにしてください。**インターネットに公開しないこと。**
> Cloudflare Access を前段に置かない限り、`8765` を Tunnel の ingress に
> 追加しないこと。

再起動をまたいで動かし続けるかはオペレータの判断です（macOS ならユーザー
launchd agent、Linux なら systemd unit）。このリポジトリは何もインストールしません。

### 3.3 各デバイスの MCP クライアントを向ける

Claude Code（`~/.claude/settings.json` またはプロジェクトの `.claude/settings.json`）:

```json
{
  "mcpServers": {
    "axonrelay": {
      "type": "http",
      "url": "http://<host>.<tailnet>.ts.net:8765/mcp"
    }
  }
}
```

Codex CLI も同じ URL を自分の MCP 設定に書きます。
各エージェントは自分の `host` / `clone_path` で `register_session` を呼び、
互いをボード上で認識します。

### 3.4 疎通確認

2台目のデバイスから。**24 tools** が出れば成功です:

```bash
python - <<'EOF'
import asyncio
from contextlib import AsyncExitStack
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = "http://<host>.<tailnet>.ts.net:8765/mcp"

async def main():
    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(streamable_http_client(URL))
        session = await stack.enter_async_context(ClientSession(read, write))
        info = await session.initialize()
        tools = await session.list_tools()
        print(info.server_info.name, len(tools.tools), "tools")

asyncio.run(main())
EOF
```

<details>
<summary>うまくいかないとき</summary>

- **接続できない** — ホスト側で `--host 0.0.0.0` を付けているか。既定の
  `127.0.0.1` だと他デバイスからは見えません。`tailscale ping <host>` も確認。
- **`Settings object has no field "host"`** — 古いコード。この不具合は
  マージ済み（`73506ac`）なので `git pull`。
- **tools が 14 個しか出ない** — 調整レイヤーが入る前のコードを動かしている。
- **`register_session` が enum エラー** — §1 の 007 未適用を参照。

</details>

### 3.5 動作イメージ

2つの clone で並行作業しているときの典型的な流れ:

```
[claude @ mbp16 /w/core]        [codex @ studio /w/core-2]
        │                                │
        │  register_session              │  register_session
        │                                │
        │                                ├─ claim_territory(["backend/alembic"])
        │                                │      -> granted: true
        │                                │
        ├─ claim_territory(["backend/alembic/versions"])
        │      -> granted: false
        │         holder: codex @ studio:/w/core-2 [feature/y]
        │                 focus="rewriting migrations"
        │                                │
        ├─ send_relay(to_actor=codex, "alembic 触ってる？") ──▶
        │                                │
        │                                ├─ read_inbox() -> 1 件
        │                                ├─ ack_relay("あと5分で解放する")
        │                                └─ release_territory()
        │                                │
        └─ claim_territory(...) -> granted: true
```

1台だけで使う場合は stdio のままで、ネットワーク設定は一切不要です:
`python -m app.mcp.server`

### 3.6 共有 clone を守る（gitsafe）

1つの clone、またはその sibling worktree で複数のエージェントが作業するなら、
[`tools/gitsafe`](../tools/gitsafe) を PATH に置きます。

**理由:** `refs/stash` はリポジトリ単位の ref なので、`git worktree add` しても
stash スタックは増えません。ある worktree での `git stash pop` が別 worktree で
退避した作業を消費し、しかも `git stash pop` はパスを1つも指名しないため、
パス claim では守れません。

```bash
export AXONRELAY_URL="http://<host>.<tailnet>.ts.net:8000"
export AXONRELAY_SESSION_ID="<register_session が返した id>"
git() { /path/to/core/tools/gitsafe git "$@"; }
```

session を登録するときは、sibling worktree が同じ stash スタックを共有していると
認識できるように、clone の git dir を渡します:

```bash
git rev-parse --path-format=absolute --git-common-dir    # -> register_session(git_dir=...)
```

サーバは相対の `.git` を `clone_path` 基準で解決して正規化します。checkout が
symlink 配下にある場合は値を `realpath` に通してください。

読み取り系の git は常に素通しです。`stash push` はメッセージに `[axonrelay:s<id>]`
を刻み、`pop` / `apply` / `drop` / `branch` は他人のタグが付いた entry を拒否し、
`stash clear` / `stash store` は常に拒否します。`reset --hard`、`clean`（dry-run 以外）、
dirty な `checkout`、`rebase`、`branch -D`（clone 単位の `refs` 資源）、
`push --force` / `+refspec` / `push --delete`（repo 全体の `remote` 資源。`origin`
ではなく push の実際の着地先で判定）はボードに照会します。git alias は展開結果で
分類し、`-C` などのグローバルオプションも扱います。

stash タグの検査はネットワーク無しで機能する（タグは stash メッセージの中にある）ので、
AxonRelay に到達できないときもこの層は残ります。到達できない場合はこの層に縮退し、
到達できたがエラー応答の場合はエラーを許可と見なさず拒否します。
`GITSAFE_ALLOW_UNSAFE=1` で1回だけ迂回でき、stderr に記録されます。

---

## 4. ダッシュボード → app.axonrelay.com

> 調整ボードだけが目的ならこの章は飛ばしてよい。

**[you]** `frontend/` を Vercel か Cloudflare Pages にデプロイ:

- Build command: `pnpm build` · Output dir: `dist` · Root: `frontend`
- 環境変数: `VITE_API_BASE=https://api.axonrelay.com`
- `app.axonrelay.com` をそのデプロイに向ける

ダッシュボードは読み取り専用です。`https://app.axonrelay.com` からの CORS は
backend 側で既に許可済み。

---

## 5. 切替と EC2 の解約

> 旧 EC2 構成がまだ生きている場合のみ。**着手前に現状を確認すること**
> （インスタンスは既に停止・EIP は解放済みの可能性があります）。

**[you]** ダウンタイムを最小化する順序:

1. 前日までに `axonrelay.com` の DNS レコード TTL を 300 秒以下に下げる
2. tunnel を上げ（§2）、`https://api.axonrelay.com/` がヘルス JSON を返すことを確認
3. `axonrelay.com` / `app.axonrelay.com` を新ダッシュボードに向け直す
4. E2E 確認（MCP でタスクを作り、ダッシュボードに出ることを見る）
5. EC2 インスタンスを停止 → スナップショット → 削除。Elastic IP を解放

## ロールバック

新構成が数日きれいに動くまで EC2 スナップショットを保持すること。tunnel の
挙動がおかしければ DNS を EC2 の IP に戻す（TTL は既に下げてある）。

---

## 関連ドキュメント

- [coordination-spec.md](../docs/coordination-spec.md) — 調整レイヤーの設計と運用プロトコル
- [mcp-server.md](../docs/mcp-server.md) — MCP tool リファレンスと接続ガイド
- [SETUP_POSTGRES.md](../SETUP_POSTGRES.md) — PostgreSQL セットアップとマイグレーション
