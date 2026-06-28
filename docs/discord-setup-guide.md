# Discord ゼロベース構築ガイド — Phase 2.6.A

このドキュメントは AxonRelay の **Discord 通知 / モバイル承認チャネル** を、Discord アカウント無し（あるいはあっても完全に分離した個人アカウント）から構築する手順です。Phase 2.6.B（Backend 実装）はこのガイドの完了後に進めます。

## 前提・制約（厳守）

- **個人 Discord アカウントを使用**。勤務先 Slack / GitHub Enterprise / 業務 SaaS と紐づかない、完全に個人用のアカウント
- 取得した **Bot Token / Public Key / Webhook URL** は **絶対にリポジトリにコミットしない**（`.env` に置き、`.gitignore` で除外）
- 招待する Bot は **自分のサーバのみ**

## 完了時に手元に残すべきもの（記録用チェックリスト）

完了時、以下を `.env` に書き写せる状態にする。控えておく場所は `.env`（ローカル）または個人パスワードマネージャ：

- [ ] **Bot Token** → `DISCORD_BOT_TOKEN`
- [ ] **Application ID** → `DISCORD_APPLICATION_ID`
- [ ] **Public Key** → `DISCORD_PUBLIC_KEY`
- [ ] **#pending Webhook URL** → `DISCORD_WEBHOOK_PENDING`
- [ ] **#completed Webhook URL** → `DISCORD_WEBHOOK_COMPLETED`
- [ ] **#audit Webhook URL** → `DISCORD_WEBHOOK_AUDIT`

---

## ステップ 1: Discord 個人アカウントの準備

1. https://discord.com にアクセス
2. 既に個人アカウントがあればそれを使う（業務と無関係なものに限る）
3. 無ければ **Register** からメール / ユーザー名 / 生年月日で作成
4. メール認証を済ませる

**完了条件:** Discord に自分のアカウントでログインできる状態

---

## ステップ 2: 個人サーバ作成

1. Discord デスクトップ or Web 版を開く
2. 左のサーバー一覧の **"+" アイコン**（"サーバーを追加"）をクリック
3. **"オリジナルの作成"** を選択
4. **"自分と友達のため"** を選択
5. サーバー名: `AxonRelay`（好きな名前で OK）
6. **"作成"** をクリック

**完了条件:** `AxonRelay` という個人サーバが左のサーバー一覧に表示されている

---

## ステップ 3: チャンネル作成

サーバーを開いた状態で、左カラムのチャンネル一覧にカーソルを当てる → カテゴリの "+" アイコンから新規チャンネル作成。

最小構成（まずこの 3 つで開始可、後から増やせる）:

| チャンネル名 | 種別 | 用途 |
|---|---|---|
| `pending` | テキスト | 承認待ちタスクの通知（モバイルで操作） |
| `completed` | テキスト | 承認・差戻し結果の台帳 |
| `audit` | テキスト | 監査用（Bot 以外の書き込み禁止予定） |

将来追加（オプション）:
- `source-claude-code` — Claude Code 由来のタスクのみ
- `source-langgraph` — 自前 LangGraph 由来のタスクのみ

各チャンネル設定（歯車）→ 権限 → `@everyone` の **"メッセージを送信"** を OFF にしておくと事故防止になります（`audit` だけでも OK）。

**完了条件:** `#pending` / `#completed` / `#audit` の 3 チャンネルが見える

---

## ステップ 4: Discord Developer Portal で Application 作成

1. https://discord.com/developers/applications にアクセス（同じアカウントで自動ログイン）
2. 右上 **"New Application"** をクリック
3. 名前: `AxonRelay`（好きな名前で OK）
4. **"Create"** をクリック

開いた画面が Application の管理画面。

**完了条件:** `AxonRelay` Application の管理画面が開いている

---

## ステップ 5: Application ID と Public Key を控える

Application 管理画面の **"General Information"** タブ：

- **Application ID** をコピー → `DISCORD_APPLICATION_ID` にメモ
- **Public Key** をコピー → `DISCORD_PUBLIC_KEY` にメモ

**完了条件:** チェックリストの該当 2 項目が埋まっている

---

## ステップ 6: Bot を追加・Token 取得

1. 左メニューの **"Bot"** タブをクリック
2. **"Reset Token"** ボタンをクリック → 確認 → 新しい Token が表示される
3. **その場でコピー**（一度しか見えない、画面を閉じると再リセットが必要）→ `DISCORD_BOT_TOKEN` にメモ
4. 下部のスイッチ群で：
   - **MESSAGE CONTENT INTENT** を ON
   - 残りはデフォルトで OK

**完了条件:** `DISCORD_BOT_TOKEN` がメモされ、Message Content Intent が ON

---

## ステップ 7: Bot 招待 URL の生成

1. 左メニューの **"OAuth2"** タブをクリック
2. **"OAuth2 URL Generator"** までスクロール
3. **Scopes** で以下にチェック：
   - `bot`
   - `applications.commands`
4. 下に **Bot Permissions** が出るので、最低限：
   - `Send Messages`
   - `Embed Links`
   - `Read Message History`
   - `Use Slash Commands`
5. **"Generated URL"** をコピー

**完了条件:** Bot 招待用の URL がコピーボードにある

---

## ステップ 8: Bot をサーバに招待

1. ステップ 7 でコピーした URL をブラウザで開く
2. **"サーバーに追加"** で先ほど作った `AxonRelay` サーバを選択
3. 権限を確認して **"認証"** をクリック
4. CAPTCHA を解く

**完了条件:** Discord サーバの右側メンバー一覧に `AxonRelay` Bot が表示されている

---

## ステップ 9: 各チャンネルの Webhook URL を取得

`#pending` / `#completed` / `#audit` それぞれで以下を実行：

1. チャンネル名横の **歯車アイコン**（"チャンネルの編集"）をクリック
2. 左メニュー **"連携サービス"** をクリック
3. **"ウェブフックを作成"** をクリック
4. 名前: `AxonRelay-{channel-name}`（例: `AxonRelay-pending`）
5. **"ウェブフック URL をコピー"** をクリック
6. それぞれを `DISCORD_WEBHOOK_PENDING` / `_COMPLETED` / `_AUDIT` にメモ

**完了条件:** 3 つの Webhook URL がメモされている

---

## ステップ 10: Interactions Endpoint URL（Phase 2.6.B 完了後に設定）

> **このステップは今は実施しない。** Backend 実装（Phase 2.6.B）で `https://api.axonrelay.com/webhooks/discord/interactions` のエンドポイントを公開した後に戻ってきます。

将来の手順:
1. Application 管理画面 **"General Information"** に戻る
2. **"Interactions Endpoint URL"** に `https://api.axonrelay.com/webhooks/discord/interactions` を入力
3. **"Save Changes"** をクリック → Discord が PING を送って検証
4. 検証が通れば緑のチェックマーク

**現時点の完了条件:** ステップ 1〜9 が完了し、`.env` に必要な値が控えてある

---

## ステップ 11: スマホ Discord アプリ設定

1. App Store / Google Play から Discord をインストール
2. 同じアカウントでログイン
3. `AxonRelay` サーバを開く
4. サーバー名横の **"..."** メニュー → **"通知設定"**
5. **"すべてのメッセージ"** を選択（Bot からの全通知を受け取る）
6. iOS の場合: 設定 → 通知 → Discord → 通知許可・サウンド・バナーを ON
7. テスト: PC 側 Discord で `#pending` に何かメッセージを送ってみる → スマホに通知が来れば OK

**完了条件:** `#pending` への投稿がスマホにプッシュ通知で届く

---

## チェックリスト（最終）

ここまで終わると、`.env` の Discord セクションが以下の状態になっているはず：

```bash
DISCORD_BOT_TOKEN=MTIz...（控えた値）
DISCORD_APPLICATION_ID=12345...
DISCORD_PUBLIC_KEY=abc123...
DISCORD_WEBHOOK_PENDING=https://discord.com/api/webhooks/...
DISCORD_WEBHOOK_COMPLETED=https://discord.com/api/webhooks/...
DISCORD_WEBHOOK_AUDIT=https://discord.com/api/webhooks/...
```

そして手元には：

- [ ] `AxonRelay` 個人 Discord サーバ
- [ ] `#pending` / `#completed` / `#audit` チャンネル
- [ ] `AxonRelay` Bot がサーバに参加
- [ ] スマホで通知を受信できる状態

すべて揃ったら、**Phase 2.6.B（Backend Discord 統合実装）** に進めます。

---

## 困ったとき

- **Bot Token を見失った** → Bot タブで再度 "Reset Token" → 新しい Token を取得し `.env` を更新
- **Bot がサーバに見えない** → 招待 URL を再生成し、招待時にスコープが `bot` を含んでいるか確認
- **Webhook 通知が来ない** → Webhook URL の末尾までコピーされているか、サーバ側のチャンネル設定で Bot のメッセージ送信が許可されているか確認
- **スマホ通知が遅い / 来ない** → サーバ通知設定が "すべて" になっているか、OS 側で Discord の通知が許可されているか確認
