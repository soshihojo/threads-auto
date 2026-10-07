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
    # ★上の層。名前と中身が決まるまでは売らん（決まってへんもんを台帳に置いても
    #   どっかの文面が勝手に拾う。実際、廃止した一問鑑定199円が台本に残って事故っとる）
}
