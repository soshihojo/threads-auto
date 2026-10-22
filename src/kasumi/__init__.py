"""かすみ（推し活占い）のLINEボットと診断ページ。

椿とは別ブランド・別のLINE公式アカウント。椿のコード（line_bot / web_diag / store）の
動きは変えず、このパッケージの中だけで完結させる。

- 診断ページ：GET /kasumi/shindan → POST /kasumi/shindan/api（diag.py）
- LINE：POST /webhook/kasumi（bot.py）。鍵は LINE_CHANNEL_SECRET_KASUMI / LINE_CHANNEL_ACCESS_TOKEN_KASUMI
- 保存：kasumi_ で始まる表（store.py）。椿の表には書かない
- 文面：texts.py（oshikatsu-uranai/docs/15_診断とLINEの文面.md と同じ中身）

目的は「売ること」ではなく、LINEの1対1で推し活の悩みを聞いてデータを溜めること。
ボットは商品の案内をしない。
"""
