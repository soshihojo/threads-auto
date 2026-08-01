# デプロイ手順（どこからでも使えるようにする）

構成：
- **共有ストア**：Google Sheets（ダッシュボードとActionsが同じキューを読む）
- **ダッシュボード**：Streamlit Community Cloud（無料・URLでどこからでも）
- **裏方**：GitHub Actions（返信ポーリング2h / 予約投稿15分 / 学習3日）

所要：30〜40分。順番にやればOK。

---

## STEP 1. Google Sheets を共有ストアにする

### 1-1. サービスアカウントを作る（無料）
1. https://console.cloud.google.com/ にログイン → 上部でプロジェクト作成（名前は何でも可）
2. 「APIとサービス」→「ライブラリ」→ **Google Sheets API** を検索して**有効化**
3. 「APIとサービス」→「認証情報」→「認証情報を作成」→「**サービスアカウント**」→ 名前を付けて作成
4. 作ったサービスアカウントを開く →「**キー**」タブ →「鍵を追加」→「新しい鍵」→「**JSON**」→ ダウンロード
   （このJSONファイルが認証情報。大事に保管）
5. サービスアカウントの**メールアドレス**（`xxxx@xxxx.iam.gserviceaccount.com`）をコピー

### 1-2. スプレッドシートを用意
1. https://sheets.google.com/ で新規スプレッドシート作成（名前は「threads-store」等）
2. 右上「共有」→ さっきの**サービスアカウントのメール**を**編集者**で追加
3. URL `https://docs.google.com/spreadsheets/d/<ここがKEY>/edit` の **KEY** をコピー

→ シートのタブ（posts/leads/scheduled_posts 等）は初回接続時に自動作成されます。

### 1-3. ローカルで接続テスト
`.env` に追記して確認：
```
STORE_BACKEND=sheets
GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/ダウンロードしたサービスアカウント.json
GOOGLE_SHEET_KEY=<コピーしたKEY>
```
```bash
python -m src.main check-store
# → ✅ 読み書きOK が出れば成功（シートにタブが自動生成される）
```

---

## STEP 2. GitHub にプッシュ

```bash
cd ~/Desktop/threads-auto
git init && git add -A && git commit -m "Threads automation"
# GitHubで空のリポジトリを作成してから↓
git remote add origin https://github.com/<あなた>/threads-auto.git
git push -u origin main
```
※ `.env` と `data/` は `.gitignore` 済みなので**秘密情報は push されません**。

---

## STEP 3. Streamlit Community Cloud にダッシュボードを載せる（無料）

1. https://share.streamlit.io/ に**GitHubアカウントでサインイン**
2. 「**New app**」→ リポジトリ `threads-auto` / ブランチ `main` / メインファイル `app.py` を選択
3. 「**Advanced settings → Secrets**」に以下を貼る（TOML形式）：

```toml
STORE_BACKEND = "sheets"
GOOGLE_SHEET_KEY = "＜シートのKEY＞"
THREADS_ACCESS_TOKEN = "＜長期トークン＞"
THREADS_USER_ID = "26501905949488652"
ANTHROPIC_API_KEY = "＜Anthropicキー＞"
APP_PASSWORD = "＜好きなログインパスワード＞"
CHATWORK_API_TOKEN = "＜任意＞"
CHATWORK_ROOM_ID = "＜任意＞"

# サービスアカウントJSONは丸ごと文字列で（三連クォート）
GOOGLE_SERVICE_ACCOUNT_JSON = '''
{
  "type": "service_account",
  ... ダウンロードしたJSONの中身を全部 ...
}
'''
```
4. 「**Deploy**」→ 数分でURLが発行される。スマホでもそのURLで開ける（APP_PASSWORDでログイン）。

---

## STEP 4. GitHub Actions に同じ秘密情報を登録

リポジトリの **Settings → Secrets and variables → Actions → New repository secret** で以下を登録：

| 名前 | 値 |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | サービスアカウントJSONの中身まるごと |
| `GOOGLE_SHEET_KEY` | シートのKEY |
| `THREADS_ACCESS_TOKEN` | 長期トークン |
| `THREADS_USER_ID` | 26501905949488652 |
| `ANTHROPIC_API_KEY` | Anthropicキー |
| `CHATWORK_API_TOKEN` / `CHATWORK_ROOM_ID` | 任意 |

→ 登録後、Actionsタブで `threads-scheduler` を「Run workflow」で手動実行 → ログが正常に終われば**全部つながった**。

---

## 完成後の運用フロー

```
[あなた]  Claudeで投稿を作り、Sheetsのscheduled_postsに直接書き込む
[15分毎]  Actionsが予約時刻の来たものを自動投稿
[随時]    返信から手挙げリードを検知 → Chatwork通知 → ダッシュボードのリードタブで確認
```

## トラブル時
- `check-store` が失敗 → シートをサービスアカウントに**編集者**で共有したか確認
- ダッシュボードが真っ白 → Streamlitの Secrets（特にJSONの三連クォート）を確認
- 投稿されない → Actionsの `threads-scheduler` ログ、トークン有効期限を確認
