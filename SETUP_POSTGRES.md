# PostgreSQL セットアップガイド

このドキュメントは、AxonRelay に PostgreSQL を統合した後の初回セットアップ手順を記載しています。

## 前提条件

- Docker Desktop がインストールされ、起動していること
- `.env` ファイルが作成されていること（`.env.example` からコピー）

## セットアップ手順

### 1. Docker コンテナの起動

```bash
# リポジトリのルートディレクトリで実行
docker compose up --build -d
```

これにより、以下のコンテナが起動します：
- `postgres` (PostgreSQL 16)
- `backend` (FastAPI + MCP サーバ)

### 2. データベースマイグレーションの実行

PostgreSQL が起動したら、初期マイグレーションを実行してテーブルを作成します：

```bash
# backend コンテナ内でマイグレーションを実行
docker compose exec backend alembic upgrade head
```

または、マイグレーションスクリプトを使用：

```bash
docker compose exec backend ./run_migrations.sh
```

### 3. マイグレーション確認

マイグレーションが正しく実行されたか確認：

```bash
# PostgreSQL コンテナに接続
docker compose exec postgres psql -U axonrelay -d axonrelay

# テーブル一覧を表示
\dt

# 期待されるテーブル:
# - users
# - projects
# - project_members
# - tasks
# - drafts
# - approvals
# - external_links
# - alembic_version

# 終了
\q
```

## データベーススキーマ

### テーブル構成

1. **users** - ユーザーアカウント（OAuth認証）
2. **projects** - プロジェクトワークスペース
3. **project_members** - ユーザーとプロジェクトの多対多関係（ロール付き）
4. **tasks** - AI エージェントが管理するタスク
5. **drafts** - タスクドラフトのバージョン履歴
6. **approvals** - タスクの承認/却下履歴
7. **external_links** - 外部リソース（GitHub Issue、Notion等）へのリンク

### ロール（project_members）

- `OWNER` - プロジェクトオーナー
- `ADMIN` - 管理者
- `MEMBER` - メンバー
- `REVIEWER` - レビュアー
- `VIEWER` - 閲覧のみ

### タスクステータス（tasks）

- `DRAFT` - 下書き
- `WAITING_APPROVAL` - 承認待ち
- `APPROVED` - 承認済み
- `REJECTED` - 却下
- `COMPLETED` - 完了
- `CANCELLED` - キャンセル

## 新しいマイグレーションの作成

モデルを変更した場合、新しいマイグレーションを作成：

```bash
# 自動検出でマイグレーション作成
docker compose exec backend alembic revision --autogenerate -m "変更内容の説明"

# マイグレーション適用
docker compose exec backend alembic upgrade head
```

## トラブルシューティング

### PostgreSQL に接続できない

```bash
# PostgreSQL コンテナのログを確認
docker compose logs postgres

# コンテナが起動しているか確認
docker compose ps
```

### マイグレーションエラー

```bash
# 現在のマイグレーションリビジョンを確認
docker compose exec backend alembic current

# マイグレーション履歴を確認
docker compose exec backend alembic history
```

### データベースをリセットする（開発環境のみ）

```bash
# コンテナを停止してボリュームを削除
docker compose down -v

# 再起動してマイグレーション実行
docker compose up -d
docker compose exec backend alembic upgrade head
```

## 環境変数

PostgreSQL 関連の環境変数（`.env` ファイル）：

```bash
# PostgreSQL 設定
POSTGRES_DB=axonrelay
POSTGRES_USER=axonrelay
POSTGRES_PASSWORD=axonrelay_dev  # 本番環境では強力なパスワードに変更

# データベース接続 URL
DATABASE_URL=postgresql://axonrelay:axonrelay_dev@postgres:5432/axonrelay
```

**本番環境では**、`POSTGRES_PASSWORD` を強力なパスワードに変更してください。
