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
import re
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


# 鑑定書・月詠みを出したあとに必ず表示する注意。PDFだけ渡して終わりにする事故を防ぐ
_DELIVERY_REMINDER = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  まだ終わりやない。次を必ずやること
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1) 本文を読んで中身を確かめる
   ・宿名の漏れ・保証表現・相談者を責める書き方
   ・モデル名や「執筆者」などの署名の混入（絶対に納品したらあかん）
   ・章の重複や、生成モデルの独り言の混入
2) 上に出た【納品文】を読んで直す ← 自動生成されたそのままでは送らない
   ・その人が一番知りたがっていた問いに、どの章で答えたか
   ・次に読む章と、そこに何が入っているか
   ・相談の中でその人が自分でやっていた良い一手への言及
   ・締めは【感想を聞く】で終える（「遠慮せず聞いてな」で終わらせない）
3) PDFと納品文を、必ずセットでLINEに送る（PDFだけ送らない）
4) 納品文に「月詠み」という商品名・価格・決済リンクを出さない（funnel ②-1）
   ★ただし締めの「線引き」一行は必ず残す。場があることだけ示唆する
   「その先の一手を受けとる場は、ちゃんと別に用意してある。そこだけ、今のうちに言うとくで」
   ★線を消したら、購入後の相談を無料で配ることになって、月額に入る理由が消える
   ★商品名を出したら、その場で商品として意識されて、鑑定書が次を売る前振りに見える
   （2026-08-17：線引きを丸ごと削っとった。値段を出さんことと線を引かんことを
     取り違えたせいや。線は引く。名前と値段とリンクだけ出さん）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""


def _strip_honorific(name: str) -> str:
    """--name に付いてきた敬称を外す。

    ★★2026-08-30：--name に「ゆみさん」と敬称込みで渡してしもた。
      生成側（shiomi.build_calendar_html / kantei のPDF名）は自分で「さん」を足す作りやから、
      表紙が【「ゆみさんさんのために」】になった。★実際に納品直前まで行った。
      ★呼び名は敬称抜きで渡すんが決まりや。渡し間違えたら、ここで黙って直して、印を出す。
    """
    raw = str(name).strip()
    # ★外すんは【さん・様】だけや。生成側が足すんがこの二つやから、二重になるんはここだけ。
    #   ★★「ちゃん」「くん」は外さん。呼び名の一部やからや（「まきちゃん」を「まき」に
    #     変えてもうたら、別の名前になる。呼び名の指定はそのまま通すんが決まり）。
    #     ★その場合は「まきちゃんさん」になるんで、直せるように印だけ出す。
    cleaned = re.sub(r"(さん|様|さま)$", "", raw)
    if cleaned and cleaned != raw:
        print(f"⚠ --name の敬称を外した：「{raw}」→「{cleaned}」"
              f"（表紙とファイル名は、こっちが自動で『さん』を付ける）")
        return cleaned
    if raw.endswith(("ちゃん", "くん")):
        print(f"⚠ 呼び名が「{raw}」やと、表紙は「{raw}さん」になる。"
              f"それでええか確かめること（呼び名はそのまま通す決まりやから、こっちでは外さん）")
    return raw


def cmd_kantei(args: argparse.Namespace) -> None:
    """個別鑑定（有料）: 章立て約10,000字の鑑定文を生成し、和風デザインのPDFを出力。"""
    from . import kantei
    details = Path(args.details_file).read_text(encoding="utf-8")
    name = _strip_honorific(args.name)
    res = kantei.make_kantei(name, args.me, args.him, details)
    print(f"→ LINE公式アプリのチャットからPDFを添付して送付: {res['pdf']}")
    print("\n" + "━" * 24 + " 納品文（LINEにそのまま貼る） " + "━" * 24)
    print(res["note"])
    print("━" * 72)
    print(_DELIVERY_REMINDER)
    # ★2026-08-21：月詠みの案内は、買うた商品で値段が分かれる。ここで一度だけ念を押す。
    print("🌙 感想が返ってきた時の月詠みは【月5,980円】や（★2026-08-26に一本化）")
    print("   https://buy.stripe.com/dRmdR88gCghlbf682e53O09")
    print("   ★ただし、この人が潮見（九十日の暦つき）を買うとったら【5,980円】の方や。")
    print("     kantei_out に『九十日の暦_名前.pdf』があるかどうかで見分ける")
    print("━" * 72)


def cmd_shiomi(args: argparse.Namespace) -> None:
    """潮見（29,800円→9,800円）: 個別鑑定書＋九十日の暦を、まとめて一回で作る。

    ★2026-08-20 新設。それまでは kantei を叩いてから shiomi を python -c で直接呼んどった。
      二段構えやと、暦を作り忘れる／暦の納品文が抜ける、いう事故が起きる。実際に起きた。
      潮見は【三点セット】で一つの商品や。作るのも一回にする。
    """
    from . import kantei, shiomi
    details = Path(args.details_file).read_text(encoding="utf-8")

    name = _strip_honorific(args.name)
    res = kantei.make_kantei(name, args.me, args.him, details)
    cal = shiomi.make_shiomi(name, args.me, args.him, details)

    print("\n" + "━" * 72)
    print("📦 潮見は三点セットや。この順番で送る:")
    print(f"   1) 個別鑑定書 PDF … {res['pdf']}")
    print(f"   2) 九十日の暦 PDF … {cal['pdf']}")
    print(f"      （LINEで開きやすいんは画像の方や … {cal['png']}）")
    print("━" * 72)

    print("\n" + "━" * 20 + " 納品文①（鑑定書と一緒に貼る） " + "━" * 20)
    print(res["note"])
    print("\n" + "━" * 20 + " 納品文②（暦と一緒に貼る・別便） " + "━" * 20)
    print(cal["note"])
    print("━" * 72)
    if cal["problems"]:
        print(f"⚠ 暦の自動検査に{len(cal['problems'])}件。中身を確かめてから送ること")
        for x in cal["problems"]:
            print(f"   ・{x}")
    print(_DELIVERY_REMINDER)
    print("★潮見だけの追加確認")
    print("  ・暦の行の主語が、全部あんた（相談者）になっとるか")
    print("  ・「彼から連絡が来る週」の類が一行も無いか")
    print("  ・暦に出る日付が、今日から九十日の範囲に収まっとるか（過去日が混じる事故があった）")
    print("  ・★納品文は二通ある。鑑定書だけ送って暦の便を忘れんこと")
    print("━" * 72)


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
    print(_DELIVERY_REMINDER)


def cmd_join(args: argparse.Namespace) -> None:
    """月詠みに入ってくれた人を、会員として登録する。

    ★2026-08-21 新設。それまでは手でシートに足しとった。
      せやから登録が遅れる。実際、ゆきえは入会した朝の時点でまだ会員表に無かった。
      ★会員表に無い＝ダッシュボードの「返信で止まっとる人」に出てこん、いうことや。
      入会したその日にいちばん手厚うせなあかん人が、リストから漏れる。逆さまや。

    やること三つを、一回で済ませる。
      1) 会員表に足す（番号は自動。呼び名はLINEの表示名をそのまま使う）
      2) 生年月日を line_users から拾うて入れる（★ここが会員とLINEの紐付けの鍵になる）
      3) 納品済みの鑑定書があったら、返信生成の参照資料として一緒に入れる
    """
    import re as _re
    from datetime import datetime
    from . import store
    from .kantei import OUT_DIR

    users = store.list_line_users() if hasattr(store, "list_line_users") else []
    if not users:
        from . import store_sheets
        users = store_sheets._records("line_users")
    hit = [u for u in users if str(u.get("display_name", "")).strip() == args.line_name]
    if len(hit) != 1:
        print(f"❌ LINEの表示名『{args.line_name}』が {len(hit)} 件見つかった。1件やないと登録できん")
        for u in hit:
            print(f"   - {u.get('display_name')} / {u.get('user_id')}")
        return
    u = hit[0]
    me = args.me or str(u.get("me_birth", "")).strip()
    him = args.him or str(u.get("him_birth", "")).strip()
    if not (me and him):
        print("❌ 生年月日が足りん。--me と --him で渡すか、先にWeb診断を通してもらうこと")
        return

    # 番号は「既にある nickname の頭の数字」の次にする。表示名はLINEのものをそのまま使う
    nums = []
    for m in store.list_members():
        mm = _re.search(r"(\d{1,3})", str(m.get("nickname", "")))
        if mm:
            nums.append(int(mm.group(1)))
    nickname = f"{max(nums) + 1 if nums else 1:02d}-{args.line_name}"

    note = f"月詠み入会 {datetime.now().strftime('%Y-%m-%d')}"
    if args.plan:
        note += f"／{args.plan}"
    # ★2026-08-26：入会の時点でLINEの user_id を控えとく。
    #   生年月日は、あとで会員が「新しい人を視てほしい」と送ってきた時に上書きされる。
    #   user_id は替わらんので、そっちを本線にする。
    _u = None
    try:
        _u = store.find_line_user_by_births(me, him)
    except Exception:
        pass
    _uid = str(_u.get("user_id")) if _u else ""
    try:
        mid = store.add_member(nickname, me, him, note, line_user_id=_uid)
    except TypeError:          # 古いバックエンド（sqlite）向けの逃げ道
        mid = store.add_member(nickname, me, him, note)
    print(f"✅ 会員登録: id={mid}  {nickname}")
    print(f"   生年月日 あんた={me} / 彼={him}")
    if _uid:
        print(f"   LINE紐付け: 「{_u.get('display_name')}」 {_uid[:16]}… ← user_idで固定した")
    else:
        print("   ⚠️ LINEが見つからん。生年月日が診断の時と違う可能性がある。")
        print("      あとで手当てする時は store.set_member_line_user(会員id, user_id)")

    # 鑑定書を参照資料として入れる。★これが有ると無いとで、返信の精度がまるで変わる
    stem = args.kantei_name or args.line_name
    src_html = OUT_DIR / f"個別鑑定_{stem}.html"
    if not src_html.exists():
        print(f"⚠️ 鑑定書が見つからん（{src_html.name}）。--kantei-name で納品時の呼び名を渡してや")
        print("   例: --kantei-name ゆきえ")
    else:
        body = _re.sub(r"<(style|script)[^>]*>.*?</\1>", "", src_html.read_text(encoding="utf-8"),
                       flags=_re.S)
        body = _re.sub(r"<[^>]+>", "\n", body)
        body = _re.sub(r"\n{3,}", "\n\n", body).strip()
        store.add_reading(mid, "個別鑑定書", "（納品済み個別鑑定PDFの全文）", body)
        print(f"📜 鑑定書を控えに入れた（{len(body)}字）")
        cal = OUT_DIR / f"九十日の暦_{stem}.html"
        if cal.exists():
            c = _re.sub(r"<(style|script)[^>]*>.*?</\1>", "", cal.read_text(encoding="utf-8"),
                        flags=_re.S)
            c = _re.sub(r"<[^>]+>", "\n", c)
            c = _re.sub(r"\n{3,}", "\n\n", c).strip()
            store.add_reading(mid, "九十日の暦", "（納品済みの潮見の暦）", c)
            print(f"🌊 九十日の暦も控えに入れた（{len(c)}字）")
            print("   ★この人は潮見の人や。月詠みは【5,980円】の方やで")

    print("\n→ ダッシュボードの💬会員相談に、この人が出るようになった")


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
    p_shi = sub.add_parser("shiomi", help="潮見＝鑑定書＋九十日の暦を一回で作る")
    p_shi.add_argument("--name", required=True, help="購入者の呼び名（表紙に載る）")
    p_shi.add_argument("--me", required=True, help="購入者の生年月日 YYYY-MM-DD")
    p_shi.add_argument("--him", required=True, help="彼の生年月日 YYYY-MM-DD")
    p_shi.add_argument("--details-file", required=True, help="悩み詳細のテキストファイル")
    p_shi.set_defaults(func=cmd_shiomi)
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
    p_join = sub.add_parser("join", help="月詠みに入ってくれた人を会員として登録する")
    p_join.add_argument("--line-name", required=True, help="LINEの表示名（そのままの綴りで）")
    p_join.add_argument("--kantei-name", help="鑑定書を作った時の呼び名（省略時はLINEの表示名）")
    p_join.add_argument("--me", help="本人の生年月日。省略時は line_users から拾う")
    p_join.add_argument("--him", help="彼の生年月日。省略時は line_users から拾う")
    p_join.add_argument("--plan", help="月詠みの値段のメモ（例: 5,980円）")
    p_join.set_defaults(func=cmd_join)
    sub.add_parser("refresh-token").set_defaults(func=cmd_refresh_token)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
