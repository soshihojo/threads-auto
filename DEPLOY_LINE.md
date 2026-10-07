# LINE AI自動返信ボット（椿）セットアップ手順

## 仕組み
```
相談者がLINEにメッセージ
 → Webhook → このサーバー → Claudeが椿の声で返信を生成 → 自動返信（無料）
購入サイン（料金/どうしたらいい 等）や危険サイン
 → 定型で受け止め＆bot一時停止（hold）＆Chatworkへ通知 → 店主が手動でクローズ
```
- 返信（reply API）は無料・無制限。push（フォールバック）のみ月200通の無料枠を消費
- AIは料金・商品の話を一切しない。売るのは店主だけ

## STEP 1. LINE側の設定（あなたの作業）
1. https://manager.line.biz → 椿のアカウント → 設定 → **Messaging API** → 「Messaging APIを利用する」
   - プロバイダーは新規作成でOK（名前は任意。例: tsubaki）
   - 発行された **Channel secret** を控える
2. https://developers.line.biz → 同チャネル → **Messaging API設定**タブ →
   一番下の **チャネルアクセストークン（長期）** を「発行」して控える
3. LINE Official Account Manager → 設定 → **応答設定**：
   - あいさつメッセージ: **オン**（Day0が届く）
   - Webhook: **オン**
   - 応答メッセージ: **オフ**（ボットと二重返信になるため）
   - チャット: オンのまま（手動クローズに使う）

## STEP 2. サーバーのデプロイ（Render・無料）
1. https://render.com にGitHubでサインアップ
2. **New → Web Service** → リポジトリ `threads-auto` を選択
3. 設定：
   - Runtime: Python / Region: Singapore
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn line_app:app --host 0.0.0.0 --port $PORT`
   - Instance Type: **Free**
4. **Environment variables** に以下を設定：

| KEY | VALUE |
|---|---|
| LINE_CHANNEL_SECRET | STEP1-1の値 |
| LINE_CHANNEL_ACCESS_TOKEN | STEP1-2の値 |
| ANTHROPIC_API_KEY | （既存のもの） |
| STORE_BACKEND | sheets |
| GOOGLE_SHEET_KEY | （既存のもの） |
| GOOGLE_SERVICE_ACCOUNT_JSON | service_account.json の中身をJSON文字列で丸ごと |
| CHATWORK_API_TOKEN | （既存のもの） |
| CHATWORK_ROOM_ID | （既存のもの） |
| LINE_BOT_ENABLED | 1 |
| CLAUDE_MODEL | （任意）未設定なら claude-opus-4-8（LINE以外の生成） |
| LINE_BOT_MODEL | （任意）未設定なら claude-sonnet-5（LINE自動返信） |

5. デプロイ完了後のURLを控える

### ★★★今、実際に動いとるURL（2026-10-07 時点で確認済み）

```
https://threads-auto-5c6q.onrender.com
```

　・`/`          … 生きとるかの確認。`{"ok":true,"rev":"<コミットのsha>"}` が返る
　　★rev が push したコミットと揃っとったら、デプロイは通っとる
　・`/webhook`   … LINEのWebhookの宛先
　・`/shindan`   … 無料診断のページ
　・`/tokushoho` … 特商法の表記（★Stripeの決済ページからここへ飛ばす）

★ここに書いてあったURL（`tsubaki-line.onrender.com`）は、もう存在せん。
　全部のパスが404を返す。★サービス名が変わった時に、ここを直し忘れたらしい。
　★★実害：生きとるか確かめるつもりで404を見て、「ボットが落ちた」と誤解する。
　　（2026-10-07に実際にやった。ボットは普通に動いとった）
　★★★RenderのURLが変わったら、必ずここと、下の三つを一緒に直すこと。
　　① LINE DevelopersのWebhook URL
　　② Stripeの決済ページから特商法へのリンク（※法定表記が開けんくなる）
　　③ UptimeRobot などのスリープ対策の監視先

## STEP 3. Webhookをつなぐ
1. LINE Developers → Messaging API設定 → **Webhook URL** に `https://<RenderのURL>/webhook` を設定
2. 「検証」を押して Success を確認 → 「Webhookの利用」をオン
3. 自分のLINEから椿に試しにメッセージ → 椿の返信が来れば完了

## STEP 4. スリープ対策（無料プランは15分無通信で休眠する）
- https://uptimerobot.com （無料）で、RenderのURL（`/`）を **5分間隔で監視** に登録
- これで常時起きた状態になり、返信の取りこぼしを防ぐ

## 運用
- **botの一時停止/再開**: Googleシートの `line_users` タブ → 該当ユーザーの `bot` 列
  - `on`=自動返信 / `hold`=停止（購入・危険サインで自動的にこうなる）/ `off`=常時停止
  - クローズが終わったら `on` に戻す
- **会話ログ**: シートの `line_chats` タブに全やりとりが残る
- **全体停止**: Renderの環境変数 `LINE_BOT_ENABLED=0` にして再デプロイ
- 通知はChatworkに届く（購入サイン/危険サイン/停止中ユーザーの発言）
