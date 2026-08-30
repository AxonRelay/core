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

## 手動同期（AxonRelay を立てていないとき / 単独 clone のとき）

以下は調整レイヤーを使わない場合の従来手順。

同じ `AxonRelay/core` を **複数の場所にクローン**している場合（別マシン、worktree、CI 用、レビュー用など）、`tmp/` の中身は **git では同期されません**。

意図的な手動同期の手順例：

### A. 単純コピー&ペースト

最も簡単。`session-progress.md` の中身を選択してコピー → 別クローン側で上書きペースト。

### B. ファイルを直接転送

```bash
# 別マシンのクローンへ scp
scp tmp/session-progress.md user@otherhost:/path/to/AxonRelay/core/tmp/session-progress.md

# 同一マシンの別 worktree へ
cp tmp/session-progress.md ../another-clone/AxonRelay/core/tmp/
```

### C. クラウドストレージ経由

iCloud / Dropbox / Drive などの個人ストレージにシンボリックリンクで同期する。
（**※ 勤務先のクラウドは使わない** — 個人 PoC の constraint #3 により、勤務先関連の同期経路は禁止）

### D. Gist (private) 経由（Codex / 別レビュアーに見せる用）

```bash
gh gist create --secret tmp/session-progress.md
# 別クローン側で
gh gist view <gist-id> --filename session-progress.md > tmp/session-progress.md
```

---

## 同期時の衝突回避のために

複数クローンで同時に編集すると衝突しがちなので、`session-progress.md` の冒頭に以下を入れておくと安全：

```markdown
> Last updated: 2026-04-30 14:30 JST on host `mbp16`
> Source clone: /Users/foo/workspace/AxonRelay/core
```

「最新の進捗はどのクローン由来か」を一目で判別できるようにする。

---

## なぜ `tmp/` を git 管理外にするのか

- 作業メモは試行錯誤を含むので、commit history に流すと PR / レビューでノイズになる
- セッション中の中間思考が公開リポジトリに残ることを避けたい
- 共有したい結論・仕様は明示的にこのフォルダ**の外**（`docs/` 等）に転記してから commit するのが規律
- `tmp/README.md` だけ tracked なのは、この運用ルール自体を共有するため
