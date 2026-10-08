"""Current new-customer prices. Existing subscription amounts come from billing."""
PRODUCTS = {
    "kantei": {"name": "個別鑑定", "price": 3980, "billing": "一回", "channel": "自動・手動案内"},
    "shiomi": {"name": "潮見", "price": 9800, "billing": "一回", "channel": "自動・手動案内"},
    "kamae": {"name": "構え", "price": 16800, "billing": "一回", "channel": "手動案内の実績あり"},
    # ★★★2026-10-07：月詠みを「相談し放題」から【週の一手＋相談月10通】へ改めた。
    #   ★層の定義は config.yaml の members.plans、勘定は src/membership.py にある。
    #     ここは「今いくらで売っとるか」の台帳や。値段だけを置く。
    #   ★★既に入っとる人は据え置き（相談の回数に制限なし）。遡らせん。
    "tsukiyomi": {"name": "月詠み", "price": 5980, "billing": "月額・新規",
                  "channel": "納品後の感想を受けて案内"},
    # ★★★2026-10-07：上の層。名前と決済リンクが決まったので台帳に入れた。
    #   中身＝週の一手（月4本）＋相談し放題＋毎月の三十日の暦。★20席で切っとる。
    #   ★し放題を売る層は席を切らんと設計が壊れる（実測：会員1人あたり月35通。
    #     20席で月700通＝1日23通が、手の届く上限や）
    "shiogoyomi": {"name": "椿の潮暦", "price": 19800, "billing": "月額・新規",
                   "channel": "鑑定書の感想が返ってきた時に月詠みと二つ並べて案内（20席）"},
}
