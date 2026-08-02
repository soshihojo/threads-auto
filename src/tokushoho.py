"""特定商取引法に基づく表記のページ。

月額会員（椿の月詠み）の決済ページから遷移させる法定表記。
内容を変えるときは、必ずここだけを直す（複数箇所に散らさない）。

※このページの記載内容は事業者の責任で管理すること。
　住所・電話番号・氏名は法律上の開示義務があり、省略や虚偽は認められない。
"""
from __future__ import annotations

# ---- 事業者情報（ここを書き換える） ----
SELLER = "adhood　北城壮志"
MANAGER = "北城壮志"
ADDRESS = "大阪府大阪市東淀川区下新庄4-7-11"
TEL = "080-3946-8632"
EMAIL = "soshihojo@ad-hood.com"

# ---- 商品情報 ----
ITEMS = [
    ("販売価格",
     "個別鑑定書：2,980円（税込）<br>"
     "月額会員「椿の月詠み」：月額 2,980円（税込）<br>"
     "※価格は各商品の販売ページに表示します。"),
    ("商品代金以外の必要料金",
     "インターネット接続に必要な通信料金は、お客様のご負担となります。<br>"
     "その他の手数料はいただきません。"),
    ("お支払い方法",
     "クレジットカード決済（決済代行サービスを利用します）"),
    ("お支払い時期",
     "個別鑑定書：お申し込み時に決済されます。<br>"
     "月額会員：お申し込み時に初回分を決済し、以降は毎月同日に自動更新されます。"),
    ("商品の引渡時期",
     "個別鑑定書：お申し込みから数日以内に、LINEにPDFでお届けします。<br>"
     "月額会員：毎月1回、その月の鑑定書をLINEにPDFでお届けします。"
     "あわせて、期間中はLINEでのご相談を回数の制限なくお受けします。"),
    ("解約について",
     "月額会員は、LINEでご連絡いただくことで解約できます。<br>"
     "<strong>次回更新日の3営業日前まで</strong>にご連絡ください。"
     "それ以降のご連絡の場合、当月分は決済され、その翌月から解約となります。<br>"
     "解約後も、すでにお届けした鑑定書はお手元に残ります。"),
    ("返品・キャンセルについて",
     "商品の性質上（お客様専用に作成するデジタルコンテンツのため）、"
     "お届け後のお客様都合による返品・返金はお受けできません。<br>"
     "ただし、決済が完了しているのに商品が届かない場合や、"
     "内容に不備があった場合は、下記の連絡先までご連絡ください。誠実に対応いたします。"),
    ("動作環境",
     "PDFファイルを閲覧できる環境、およびLINEアプリが必要です。"),
]

NOTES = [
    "鑑定は占いによるものであり、結果を保証するものではありません。",
    "医療・法律・投資など、専門的な判断を要する事柄についての助言は行いません。",
    "20歳未満の方はご利用いただけません。",
]

PAGE_HTML = """<!doctype html><html lang="ja"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>特定商取引法に基づく表記｜椿</title>
<style>
  :root {{ --ink:#2b2b33; --sub:#6b6b78; --line:#e6e2dd; --bg:#faf8f5; --accent:#6b4c7a; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:0; background:var(--bg); color:var(--ink);
         font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif;
         line-height:1.9; font-size:15px; }}
  .wrap {{ max-width:720px; margin:0 auto; padding:32px 20px 64px; }}
  h1 {{ font-size:19px; letter-spacing:.04em; margin:0 0 6px; }}
  .lead {{ color:var(--sub); font-size:13px; margin:0 0 28px; }}
  table {{ width:100%; border-collapse:collapse; background:#fff;
           border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
  th, td {{ text-align:left; vertical-align:top; padding:14px 16px;
            border-bottom:1px solid var(--line); font-size:14px; }}
  th {{ width:34%; background:#f5f1ec; font-weight:600; color:var(--accent); white-space:nowrap; }}
  tr:last-child th, tr:last-child td {{ border-bottom:none; }}
  .notes {{ margin-top:26px; padding:16px 18px; background:#fff;
            border:1px solid var(--line); border-radius:10px; }}
  .notes h2 {{ font-size:14px; margin:0 0 8px; color:var(--accent); }}
  .notes ul {{ margin:0; padding-left:1.2em; font-size:13.5px; color:var(--sub); }}
  footer {{ margin-top:32px; text-align:center; color:var(--sub); font-size:12px; }}
  @media (max-width:520px) {{
    th, td {{ display:block; width:100%; }}
    th {{ border-bottom:none; padding-bottom:4px; }}
    td {{ padding-top:0; }}
  }}
</style></head><body>
<div class="wrap">
  <h1>特定商取引法に基づく表記</h1>
  <p class="lead">通信販売における表示事項です。</p>
  <table>
    <tr><th>販売事業者</th><td>{seller}</td></tr>
    <tr><th>運営統括責任者</th><td>{manager}</td></tr>
    <tr><th>所在地</th><td>{address}</td></tr>
    <tr><th>電話番号</th><td>{tel}<br><span style="color:#6b6b78;font-size:13px;">
        ※お問い合わせはメールまたはLINEでお願いいたします。</span></td></tr>
    <tr><th>メールアドレス</th><td>{email}</td></tr>
    {rows}
  </table>
  <div class="notes">
    <h2>ご利用にあたって</h2>
    <ul>{notes}</ul>
  </div>
  <footer>椿</footer>
</div>
</body></html>"""


def page_html() -> str:
    """特定商取引法に基づく表記のHTMLを返す。"""
    rows = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in ITEMS)
    notes = "".join(f"<li>{n}</li>" for n in NOTES)
    return PAGE_HTML.format(seller=SELLER, manager=MANAGER, address=ADDRESS,
                            tel=TEL, email=EMAIL, rows=rows, notes=notes)
