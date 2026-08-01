# Threads自動化システム（店舗オーナー向けリード獲得）

投稿の自動生成・配信、コメント返信の下書き承認、**手挙げリードの検知→Chatwork通知**までを回す。
公式Threads API（無料）を使用。LLM代（月数百円規模）以外はほぼ無料で運用できる。

## このシステムがやること

| ブロック | 機能 | 状態 |
|---|---|---|
| A. 投稿 | **Sheetsのscheduled_postsに直接書き込み**→予約時刻にrun-dueが配信 | ✅ |
| B. 返信 | 自分の投稿への返信を取得 → 返信案を生成 → **下書き承認制**で送信 | ✅ |
| C. リード | 「やりたい」等の手挙げを検知 → 蓄積 → Chatwork通知 | ✅ |
| D. 分析 | 投稿インサイト取得 → 伸びた型を学習アーカイブ | ✅（学習ループ強化は次段） |
| E. 基盤 | 長期トークン自動更新 / レート監視 / 複数アカウント | ✅ |
| F. ダッシュボード | Streamlit画面でコメント返信承認・無料診断・会員相談・会員管理 | ✅ |

## 投稿の流れ（承認モデル）

```
[随時]   Sheetsのscheduled_postsに直接書き込む（status=scheduled）
[15分毎] run-due   → 予約時刻が来たものを自動投稿
```

## ダッシュボード起動（ローカル）

```bash
streamlit run app.py     # ブラウザで開く。コメント返信・無料診断・会員相談
```

## ⚠️ ホスティング（どこからでも使う）には共有ストアが必要

現状の保存先はローカルSQLite。**ダッシュボードをクラウドに載せ、生成/投稿をGitHub Actionsで回すと、両者のDBが別物**になり予約が噛み合わない。
ホスティング時は保存先を**Google Sheets等の共有ストア**に切り替える必要がある（次段で対応）。
それまでは「ローカルでダッシュボード＋ローカルcron」または「全部Actions」のどちらかで運用する。

---

## セットアップ

### 0. 前提
- Threadsアカウント（プロアカウント推奨）
- Meta（Facebook）開発者アカウント … 無料
- Python 3.10+

### 1. Meta側でThreads APIアプリを作る（最初の関門）

1. https://developers.facebook.com/ で開発者登録（無料）
2. **マイアプリ → アプリを作成 → ユースケース「Threads API」** を選択
3. アプリに **Threads APIプロダクト** を追加
4. **権限（スコープ）** を有効化：
   - `threads_basic`（必須）
   - `threads_content_publish`（投稿に必須）
   - `threads_manage_replies`（返信の取得・送信に必須）
   - `threads_read_replies`（返信読み取り）
   - `threads_manage_insights`（インサイト取得）
5. **「Threads testers」** に自分のThreadsアカウントを追加し、Threadsアプリ側で招待を承認
6. **アクセストークンを発行**（Graph API Explorer もしくはアプリ設定の「ユーザートークン生成」から、上のスコープ付きで取得）

### 2. 短期トークン → 長期トークン（60日）に交換

短期トークンを取得したら、長期トークンに交換する：

```
GET https://graph.threads.net/access_token
  ?grant_type=th_exchange_token
  &client_secret=<アプリのシークレット>
  &access_token=<短期トークン>
```

返ってきた `access_token` が長期トークン（約60日有効）。これを `.env` に入れる。
※ 本システムは実行のたびに残り有効期限を見て**自動でリフレッシュ**する（24h経過後ならいつでも更新可）。

### 3. 環境変数

```bash
cp .env.example .env
# .env を編集してトークン等を記入
```

最低限 `THREADS_ACCESS_TOKEN` と `ANTHROPIC_API_KEY` があれば投稿生成・配信は動く。
リード通知を使うなら `CHATWORK_*` も設定。

### 4. インストール

```bash
pip install -r requirements.txt
```

### 5. 動作確認

```bash
python -m src.main check       # トークン有効性・ユーザーID・残り投稿枠を確認
python -m src.main post        # 1投稿を生成して配信（--dry-run で配信せず本文だけ確認）
python -m src.main replies     # 返信を取得し、リード検知＆返信下書きを作成
python -m src.main approve     # 作成済みの返信下書きを承認/送信
```

---

## 運用（無料・定期実行）

GitHub Actions のcronで定期起動する（`.github/workflows/` 参照）。サーバー常駐は不要。
- 投稿：1日2回（config.yamlの `posting.times`）
- 返信ポーリング＋リード検知：1〜数時間おき

## 重要な事実・前提

- **ジオ配信ターゲティングは不可**：Threadsに「大阪の人だけに配信」機能はない。
  代わりに本文の**地域ワード**＋**位置タグ**でアルゴ/検索の地域露出を取りにいく（config.yamlで設定）。
  ※ geo-gatingは「国単位の出し分け」かつ適格アカウント限定で、都市ターゲティングではない。
- **凍結リスク対策**：返信は既定で**下書き承認制**。
  - ⚠️ config.yaml の `safety`（`daily_post_cap` / `daily_reply_cap` / `min_seconds_between_actions`）は
    **コードから一度も参照されておらず、機能していない**（2026-08-02の監査で判明）。
    投稿数の自動制限はかからないので、予約を入れる本数は人間が管理すること。
- **このオーディエンス仮説は未検証**：Threadsに店舗オーナーがどれだけ居るかは要検証。
  まず投稿→手挙げ検知ループを回し、リードが付くか自体を測ること。
