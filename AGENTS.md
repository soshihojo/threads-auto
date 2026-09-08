# 椿（つばき）事業｜作業前に読むこと

恋愛・復縁専門の占い師ペルソナ「椿」を Threads と LINE で運営する事業。

## 最初に読むファイル

**`handoff/00_移行用_全体引き継ぎ.md` を最初に読むこと。**
ビジネスの流れ、URL、コードの地図、実測データ、過去の事故、今の状態が全部そこにある。

文面（投稿・返信・鑑定書・ヒアリング）を書く時は、加えて次を読む。
読まずに書くと椿の口調（関西弁・毒舌姉御）が標準語敬語に壊れる。

- `rules_tsubaki/01_persona.md` … 口調・ペルソナ
- `rules_tsubaki/04_avoid.md` … NG（宿曜用語禁止・Markdown禁止・保証表現禁止）
- `rules_tsubaki/03_winning_elements.md` … CTAの実測
- `rules_tsubaki/08_hearing.md` … ヒアリングの型
- `funnel/claude_project_tsubaki.md` … 商品・価格・販売の原則

投稿を作る時は `02_templates.md` `05_learned.md` `06_customer_voice.md` `07_buzz_swipe.md` も読む。

## 絶対に守ること

1. **椿がAIであることを匂わせない。**モデル名・開発元・署名を一語も書かない。
2. **顧客に渡す文面にMarkdownを混ぜない。**`**` `#` `- ` はそのまま表示される。
3. **保証表現を書かない。**「必ず復縁できる」は景表法・消契法の問題。
4. **宿曜の用語を本文に出さない。**書くのは「ウチが視たら」。
5. **彼を「あいつ」「あの男」と呼ばない。**
6. **相談窓口・ホットラインを案内しない。**
7. **サブスク案内に「入らんでええ」を書かない。**
8. **時間帯の挨拶を書かない。**

## 出力の作法

- **納品文・投稿・返信は、返信本文にコードブロックで全文貼る。**
  ファイルパスを示すだけでは店主の画面に届かない。「全文が要りますか」と聞かない。
- **手で書いた文面はコードのガードを通らない。**書いたら必ず
  `tools/check_draft.py <ファイル> --name <呼び名（敬称ごと）>` を通す。
- **投稿は必ず `tools/make_posts.py` を通す。**手でTSVを組むと id と時刻がずれる。

## 環境

```bash
cd ~/Desktop/threads-auto
.venv/bin/python -m src.main check --account a   # 疎通確認
```

Pythonは `.venv/bin/python`。本番のデータは Google Sheets（`STORE_BACKEND=sheets`）。
鑑定書の生成は8〜12分かかるので、バックグラウンドで回す。

## 古いコピーを掴まないこと

同名の `rules_tsubaki` が `~/Desktop/kohaku-threads` と `~/rin-threads` にもあるが、
**どちらも古い**。最新は必ず `~/Desktop/threads-auto` 側。
