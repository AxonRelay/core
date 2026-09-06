# `tmp/` — クローンごとのローカル作業領域

このディレクトリは **このクローン専用の作業メモ置き場** です。`.gitignore` により、**この `README.md` 以外のファイルは git 追跡されません**（`tmp/*` を除外、`!tmp/README.md` だけ例外）。

---

## 使い方（慣例）

- **`tmp/session-progress.md`** — 進捗・次の TODO・判断ログ・未解決事項を集約するファイル。慣例的にここを開けば「今この作業がどこまで進んでいて、次に何をすべきか」が分かる状態にする。
- **その他のメモ・スクラッチコード・仮の出力** — 自由に置いてよい。
- **機密・認証情報は置かない**。`.env` 系はリポジトリ直下の `.env` を使う（こちらも gitignore 済）。

---

## クローン間の共有は AxonRelay 本体でやる（推奨）

> **2026-08-30 更新.** 以下の手動同期手順は、AxonRelay 自身の**調整レイヤー**（Phase 3）が
> 引き取った。複数 clone・複数デバイス・複数エージェントで作業しているなら、
> `session-progress.md` を手で配る代わりに MCP tool を使う:
>
> | 手動でやっていたこと | 対応する MCP tool |
> |---|---|
> | `session-progress.md` に進捗と focus を書く | `heartbeat_session(session_id, focus=...)` |
> | 別 clone の `session-progress.md` を読む | `get_board(repo=...)` |
> | territory 衝突を目視で確認する | `check_conflicts(repo, paths)` / `claim_territory(...)` |
> | INBOX を書いて相手に渡す | `send_relay(...)` / `read_inbox(session_id)` |
>
> 全 clone が同じ AxonRelay インスタンスを見るので、コピーも scp も gist も要らない。
> 設計と運用プロトコル: [docs/coordination-spec.md](../docs/coordination-spec.md)。
>
> このディレクトリは引き続き**このクローン限りのスクラッチ**として使う — 試行錯誤のメモ、
> 仮の出力、共有する価値のない中間物。**他の clone と共有したい状態は AxonRelay に置く。**

---

## AxonRelay を立てていないとき

調整レイヤーを使わない単独 clone なら、`tmp/session-progress.md` はこの clone だけの
メモとして使えばよい。複数 clone で同じファイルを**手で**配る運用（コピー、`scp`、
private gist）はかつてここに手順があったが、それはこのリポジトリが解こうとしている
問題そのものであり、いまは上の表の MCP tool がその役を担う。手で配る場合は、冒頭に
「いつ・どのホスト・どの clone で更新したか」を一行書いておくと、どれが最新かで迷わない。

---

## なぜ `tmp/` を git 管理外にするのか

- 作業メモは試行錯誤を含むので、commit history に流すと PR / レビューでノイズになる
- セッション中の中間思考が公開リポジトリに残ることを避けたい
- 共有したい結論・仕様は明示的にこのフォルダ**の外**（`docs/` 等）に転記してから commit するのが規律
- `tmp/README.md` だけ tracked なのは、この運用ルール自体を共有するため
