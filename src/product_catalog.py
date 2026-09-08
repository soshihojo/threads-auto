"""Current new-customer prices. Existing subscription amounts come from billing."""
PRODUCTS = {
    "kantei": {"name": "個別鑑定", "price": 3980, "billing": "一回", "channel": "自動・手動案内"},
    "shiomi": {"name": "潮見", "price": 9800, "billing": "一回", "channel": "自動・手動案内"},
    "kamae": {"name": "構え", "price": 16800, "billing": "一回", "channel": "手動案内の実績あり"},
    "tsukiyomi": {"name": "月詠み", "price": 5980, "billing": "月額・新規", "channel": "納品後の感想を受けて案内"},
}
