# Google OAuth 設定ガイド

AxonRelay で Google OAuth 認証を使用するための設定手順です。

## 前提条件

- Google アカウント
- Google Cloud Console へのアクセス権限

## 手順

### 1. Google Cloud Console でプロジェクトを作成

1. [Google Cloud Console](https://console.cloud.google.com/) にアクセス
2. 新しいプロジェクトを作成するか、既存のプロジェクトを選択
3. プロジェクト名: `AxonRelay` (任意)

### 2. OAuth 同意画面の設定

1. サイドバーから **APIs & Services** → **OAuth consent screen** を選択
2. **User Type** を選択:
   - 開発環境: **External** を選択
   - 本番環境: 組織のG Suiteアカウントがあれば **Internal** も可
3. **CREATE** をクリック

4. **OAuth consent screen** 設定:
   ```
   App name: AxonRelay
   User support email: (あなたのメールアドレス)
   Application home page: http://localhost (開発) / https://axonrelay.com (本番)
   Developer contact information: (あなたのメールアドレス)
   ```

5. **Scopes** 設定:
   - **ADD OR REMOVE SCOPES** をクリック
   - 以下のスコープを追加:
     - `userinfo.email`
     - `userinfo.profile`
     - `openid`

6. **Test users** (External の場合):
   - 開発中にアクセスを許可するGoogleアカウントを追加

7. **SAVE AND CONTINUE** をクリックして完了

### 3. OAuth 2.0 クライアント ID の作成

1. サイドバーから **APIs & Services** → **Credentials** を選択
2. **CREATE CREDENTIALS** → **OAuth client ID** をクリック
3. Application type: **Web application**
4. Name: `AxonRelay Web Client` (任意)

5. **Authorized JavaScript origins**:
   ```
   http://localhost (開発環境)
   https://axonrelay.com (本番環境)
   ```

6. **Authorized redirect URIs**:
   ```
   http://localhost/api/auth/callback/google (開発環境)
   https://axonrelay.com/api/auth/callback/google (本番環境)
   ```

7. **CREATE** をクリック

8. **クライアント ID** と **クライアント シークレット** が表示されるのでコピー

### 4. 環境変数の設定

#### ローカル開発環境

`.env` ファイルに以下を追加:

```bash
# Auth.js
AUTH_SECRET=your-random-secret-here  # 次のコマンドで生成: openssl rand -base64 32
NEXTAUTH_URL=http://localhost

# Google OAuth
GOOGLE_CLIENT_ID=your-google-client-id-here.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-google-client-secret-here
```

#### 本番環境（EC2）

EC2上の `~/core/.env` に以下を追加:

```bash
# Auth.js
AUTH_SECRET=your-production-random-secret-here  # openssl rand -base64 32
NEXTAUTH_URL=https://axonrelay.com

# Google OAuth
GOOGLE_CLIENT_ID=your-google-client-id-here.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-google-client-secret-here
```

### 5. AUTH_SECRET の生成

開発環境と本番環境で**別々の**ランダムシークレットを生成:

```bash
# macOS / Linux
openssl rand -base64 32

# 出力例:
# aB3cD4eF5gH6iJ7kL8mN9oP0qR1sT2uV3wX4yZ5aB6c=
```

## セキュリティ注意事項

- **本番環境では必ず HTTPS を使用**してください
- `GOOGLE_CLIENT_SECRET` と `AUTH_SECRET` は絶対に公開しないでください
- `.env` ファイルは `.gitignore` に含まれているか確認してください
- 本番環境では強力な `AUTH_SECRET` を使用してください（32文字以上推奨）

## トラブルシューティング

### 「redirect_uri_mismatch」エラー

Google Cloud Console の **Authorized redirect URIs** に正しいURLが設定されているか確認:
- 開発: `http://localhost/api/auth/callback/google`
- 本番: `https://axonrelay.com/api/auth/callback/google`

### 「Access blocked: This app's request is invalid」

OAuth consent screen の設定が完了していない可能性があります。手順2を再確認してください。

### テストユーザー追加後もアクセスできない

OAuth consent screen を **External** に設定している場合、テストユーザーとして追加したGoogleアカウントでログインしているか確認してください。

## 参考リンク

- [Google Cloud Console](https://console.cloud.google.com/)
- [Auth.js Documentation](https://authjs.dev/)
- [Google OAuth 2.0 Documentation](https://developers.google.com/identity/protocols/oauth2)
