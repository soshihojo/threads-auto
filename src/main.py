"""エントリポイント（CLI）。

使い方:
  python -m src.main check               トークン/ユーザー/残り枠を確認
  python -m src.main post [--dry-run]    1投稿を生成して配信
  python -m src.main replies             返信取得→リード検知→下書き作成(autoなら送信)
  python -m src.main approve             返信下書きを承認/編集/送信（対話）
  python -m src.main insights            インサイト取得→DB更新→伸びた投稿を学習記録
  python -m src.main learn               反応データを分析→勝ち要素を抽象化→ルール自動更新
  python -m src.main voice-learn         LINE相談ログから「お客様の声」を学習→投稿ルール更新
  python -m src.main refresh-token       長期トークンを更新して表示
"""
from __future__ import annotations

import argparse
from pathlib import Path

from . import analytics, content, diagnosis, replies as replies_mod, schedule as schedule_mod, store
from .config import DATA_DIR, active_profile, env, load_config
from .threads_client import ThreadsClient

TOKEN_FILE = DATA_DIR / "token.txt"


def _token() -> str:
    # 自動更新で保存したトークンがあれば優先、無ければ.env
    if TOKEN_FILE.exists():
        t = TOKEN_FILE.read_text().strip()
        if t:
            return t
    return env("THREADS_ACCESS_TOKEN", required=True)


def make_client() -> ThreadsClient:
    return ThreadsClient(_token(), env("THREADS_USER_ID"))


# ---------- commands ----------
def cmd_check(_: argparse.Namespace) -> None:
    c = make_client()
    me = c.me()
    print(f"✅ 認証OK: @{me.get('username')} (id={me.get('id')})")
    try:
        lim = c.publishing_limit()
        data = (lim.get("data") or [{}])[0]
        print(f"📊 残り枠: 投稿 {data.get('quota_usage','?')}/{data.get('config',{}).get('quota_total','?')}"
              f"  返信 {data.get('reply_quota_usage','?')}")
    except Exception as e:
        print(f"(残り枠の取得はスキップ: {e})")
    p = active_profile()
    print(f"🧭 プロファイル: {p['name']} / 地域ワード: {p.get('region_words')}")


def cmd_post(args: argparse.Namespace) -> None:
    profile = active_profile()
    case = content.pick_case()  # 実話回は実話の地域、一般論回はランダム地域（本文と位置タグで同じ地域）
    region = (case or {}).get("region") or content.pick_region()
    text = content.generate_best(region=region, case=case)
    print(f"----- 生成された投稿（地域: {region or 'なし'}）-----")
    print(text)
    print("--------------------------")
    if args.dry_run:
        print("(--dry-run のため配信しません)")
        return
    c = make_client()
    loc_id = c.first_location_id(region) if (profile.get("tag_location") and region) else None
    media_id = c.publish_thread(text, location_id=loc_id)
    store.init_db()
    store.save_post(media_id, text, profile["name"])
    print(f"🚀 配信しました: media_id={media_id}")


def cmd_replies(_: argparse.Namespace) -> None:
    c = make_client()
    stats = replies_mod.process_replies(c)
    print(f"📥 新規返信 {stats['new_replies']} / リード {stats['leads']} / 下書き {stats['drafts']} / 自動送信 {stats['auto_sent']}")
    if load_config()["replies"].get("mode") == "draft" and stats["drafts"]:
        print("→ `python -m src.main approve` で下書きを確認・送信してください。")


def cmd_approve(_: argparse.Namespace) -> None:
    store.init_db()
    drafts = store.pending_drafts()
    if not drafts:
        print("承認待ちの下書きはありません。")
        return
    c = make_client()
    print(f"承認待ち {len(drafts)} 件。各件: [y]送信 / [e]編集して送信 / [s]スキップ / [q]中断\n")
    for d in drafts:
        print(f"── @{d['username']} のコメント:\n   「{d['in_text']}」")
        print(f"   返信案: {d['draft_text']}")
        choice = input("   [y/e/s/q]> ").strip().lower()
        if choice == "q":
            break
        if choice == "s":
            store.set_draft_status(d["reply_id"], "skipped")
            continue
        text = d["draft_text"]
        if choice == "e":
            edited = input("   新しい返信文> ").strip()
            if edited:
                text = edited
                store.update_draft_text(d["reply_id"], text)
        try:
            c.reply_to(d["reply_id"], text)
            store.set_draft_status(d["reply_id"], "sent", sent=True)
            print("   ✅ 送信しました\n")
        except Exception as e:
            print(f"   ❌ 送信失敗: {e}\n")


def cmd_insights(_: argparse.Namespace) -> None:
    c = make_client()
    results = analytics.collect_insights(c)
    n = analytics.archive_winners(results)
    print(f"📈 {len(results)}件のインサイトを更新。伸びた{n}件を学習アーカイブに記録。")
    for r in sorted(results, key=lambda x: x["engagement"], reverse=True)[:5]:
        print(f"  eng {r['engagement']:.1%} | views {r['views']} | {r['text'][:30]}…")


def cmd_learn(_: argparse.Namespace) -> None:
    """反応データ（リード/エンゲージ）を分析し、勝ち要素を抽象化して学習ルールを自動更新。"""
    c = make_client()
    res = analytics.learn_and_update_rules(c)
    if not res.get("updated"):
        print(f"⏭️ 学習スキップ: {res.get('reason')}")
        return
    print(f"🧠 学習完了: 分析{res['analyzed']}件（勝ち{res['winners']}/スベり{res['losers']}）"
          f"→ {res['path']} を更新しました。")


def cmd_voice_learn(_: argparse.Namespace) -> None:
    """LINE相談ログから「お客様の声」を学習し、投稿生成ルール(06_customer_voice.md)を自動更新。"""
    from . import voice_learn
    res = voice_learn.mine_customer_voice()
    if not res.get("updated"):
        print(f"⏭️ 学習スキップ: {res.get('reason')}")
        return
    print(f"🗣️ お客様の声を学習: 相談者{res['users']}人・{res['messages']}メッセージ"
          f"→ {res['path']} を更新しました。")


def cmd_check_store(_: argparse.Namespace) -> None:
    """保存先（Sheets/SQLite）に接続できるか確認。Sheetsなら必要なシートを作成する。"""
    print(f"保存先バックエンド: {store._backend}")
    store.init_db()
    cid = store.add_candidate("接続テスト（自動削除されます）")
    found = [r for r in store.list_scheduled(status="pending_review") if str(r["id"]) == str(cid)]
    store.cancel_scheduled(cid)
    print("✅ 読み書きOK" if found else "⚠️ 書けたが読み出せず。権限/共有設定を確認")


def cmd_run_due(_: argparse.Namespace) -> None:
    c = make_client()
    stats = schedule_mod.run_due(c)
    print(f"⏰ 予約チェック: 対象 {stats['due']} / 配信 {stats['posted']} / 失敗 {stats['failed']}")


def cmd_diagnose(args: argparse.Namespace) -> None:
    """無料診断：状況＋二人の生年月日 → 椿の鑑定文を生成して表示。"""
    res = diagnosis.generate_reading(args.me, args.him, args.status, args.period, args.details)
    print(f"----- 無料診断（あなた:{res['me_shuku']} / 彼:{res['him_shuku']} / 距離:{res['distance']}）-----")
    print(res["reading"])
    print("--------------------------")


def cmd_monthly(args: argparse.Namespace) -> None:
    """月額会員向け：二人の生年月日＋今月の悩み → 今月の運気鑑定を生成して表示。"""
    res = diagnosis.generate_monthly(args.me, args.him, args.worry, args.month)
    print(f"----- {args.month}の会員鑑定（あなた:{res['me_shuku']} / 彼:{res['him_shuku']}）-----")
    print(res["reading"])
    print("--------------------------")


def cmd_kantei(args: argparse.Namespace) -> None:
    """個別鑑定（有料）: 章立て約10,000字の鑑定文を生成し、和風デザインのPDFを出力。"""
    from . import kantei
    details = Path(args.details_file).read_text(encoding="utf-8")
    res = kantei.make_kantei(args.name, args.me, args.him, details)
    print(f"→ LINE公式アプリのチャットからPDFを添付して送付: {res['pdf']}")


def _clean_member_name(nickname: str) -> str:
    """会員の登録名から管理用の連番・括弧書きを落として、客に見せる呼び名にする。
    例: 「美-02(月初ミニ鑑定)」→「美」 / 「03-田中麻衣(月初ミニ鑑定)」→「田中麻衣」"""
    import re
    s = re.sub(r"[（(].*?[）)]", "", nickname)
    s = re.sub(r"^[\d\-_\s]+|[\d\-_\s]+$", "", s)
    return s.strip() or nickname


def cmd_tsukiyomi(args: argparse.Namespace) -> None:
    """月詠み（月額会員の月次ミニ鑑定書PDF）: 登録会員を選び、鑑定書と一貫した今月の鑑定を出力。"""
    from . import kantei, store
    members = {str(m["nickname"]): m for m in store.list_members()}
    m = members.get(args.member)
    if not m:
        print(f"会員「{args.member}」が見つかりません。登録済み: {', '.join(members) or '（なし）'}")
        return
    # 納品済みの個別鑑定書（👥会員でPDF登録したもの）があれば、読み・処方箋を一貫させる
    kantei_rows = [r for r in store.list_readings(m["id"]) if r["month"] == "個別鑑定書"]
    kantei_text = str(kantei_rows[0]["reading"]) if kantei_rows else ""
    worry = args.worry
    if args.worry_file:
        worry = Path(args.worry_file).read_text(encoding="utf-8")
    # 表紙と本文の呼びかけに使う名前。★個別鑑定書で渡した呼び名と必ず揃える。
    # 会員の登録名（例「美-02(月初ミニ鑑定)」）は管理用なので、そのままでは客に見せられない。
    name = (args.name or "").strip()
    if not name:
        name = _clean_member_name(m["nickname"])
        print(f"⚠️ --name が未指定のため、登録名から推測しました: 「{name}」")
        print("   個別鑑定書で使った呼び名と違う場合は、--name で指定して作り直してください。")
    res = kantei.make_tsukiyomi(name, m["me_birth"], m["him_birth"], worry,
                                kantei_text=kantei_text, month_label=args.month or None)
    # 控えを保存（💬会員相談の返信生成が「今月の月詠み」も踏まえられるように）
    store.add_reading(m["id"], f"月詠み {res['month_label']}", worry, res["body"][:15000])
    print(f"→ LINE公式アプリのチャットからPDFを添付して送付: {res['pdf']}")


def cmd_line_sweep(args: argparse.Namespace) -> None:
    """LINEの未返信（最後が相談者の発言のまま）を拾って自動返信する（bot=onのみ）。"""
    from . import line_bot
    n = line_bot.sweep_unanswered(min_age_min=args.min_age, max_age_hours=args.max_age)
    print(f"未返信 {n}件に対応した")


def cmd_refresh_token(_: argparse.Namespace) -> None:
    c = make_client()
    data = c.refresh_long_lived_token()
    if data.get("access_token"):
        TOKEN_FILE.write_text(data["access_token"])
        print(f"🔑 トークン更新OK（{data.get('expires_in','?')}秒有効）。data/token.txt に保存。")
    else:
        print(f"更新応答: {data}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="threads-auto")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(func=cmd_check)
    p_post = sub.add_parser("post"); p_post.add_argument("--dry-run", action="store_true"); p_post.set_defaults(func=cmd_post)
    sub.add_parser("replies").set_defaults(func=cmd_replies)
    sub.add_parser("approve").set_defaults(func=cmd_approve)
    sub.add_parser("insights").set_defaults(func=cmd_insights)
    sub.add_parser("learn").set_defaults(func=cmd_learn)
    sub.add_parser("voice-learn").set_defaults(func=cmd_voice_learn)
    sub.add_parser("check-store").set_defaults(func=cmd_check_store)
    sub.add_parser("run-due").set_defaults(func=cmd_run_due)
    p_diag = sub.add_parser("diagnose")
    p_diag.add_argument("--me", required=True, help="相談者の生年月日 YYYY-MM-DD")
    p_diag.add_argument("--him", required=True, help="彼の生年月日 YYYY-MM-DD")
    p_diag.add_argument("--status", default="音信不通", help="状況（音信不通/既読スルー/冷められた/別れた 等）")
    p_diag.add_argument("--period", default="2週間", help="最後の連絡からの期間")
    p_diag.add_argument("--details", default="", help="相談者の自由記述（任意）")
    p_diag.set_defaults(func=cmd_diagnose)
    p_mon = sub.add_parser("monthly")
    p_mon.add_argument("--me", required=True, help="相談者の生年月日 YYYY-MM-DD")
    p_mon.add_argument("--him", required=True, help="彼の生年月日 YYYY-MM-DD")
    p_mon.add_argument("--worry", default="", help="今月の悩み・状況（任意）")
    p_mon.add_argument("--month", default="今月", help="対象月のラベル（例: 7月）")
    p_mon.set_defaults(func=cmd_monthly)
    p_kan = sub.add_parser("kantei")
    p_kan.add_argument("--name", required=True, help="購入者の呼び名（表紙に載る）")
    p_kan.add_argument("--me", required=True, help="購入者の生年月日 YYYY-MM-DD")
    p_kan.add_argument("--him", required=True, help="彼の生年月日 YYYY-MM-DD")
    p_kan.add_argument("--details-file", required=True, help="悩み詳細のテキストファイル")
    p_kan.set_defaults(func=cmd_kantei)
    p_tsu = sub.add_parser("tsukiyomi")
    p_tsu.add_argument("--member", required=True, help="👥会員に登録済みのニックネーム（検索用）")
    p_tsu.add_argument("--name", default="",
                       help="鑑定書に載る呼び名。★個別鑑定書で使った名前を必ず指定する"
                            "（会員の登録名には管理用の連番が入るため、そのままだと表紙に出てしまう）")
    p_tsu.add_argument("--worry", default="", help="会員の近況・今月の悩み（任意）")
    p_tsu.add_argument("--worry-file", default="", help="近況・悩みのテキストファイル（任意）")
    p_tsu.add_argument("--month", default="", help="対象月ラベル（例: 2026年8月。省略時は今月）")
    p_tsu.set_defaults(func=cmd_tsukiyomi)
    p_ls = sub.add_parser("line-sweep")
    p_ls.add_argument("--min-age", type=int, default=3, help="この分数より新しい未返信は触らない")
    p_ls.add_argument("--max-age", type=int, default=48, help="この時間より古い未返信は触らない")
    p_ls.set_defaults(func=cmd_line_sweep)
    sub.add_parser("refresh-token").set_defaults(func=cmd_refresh_token)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
