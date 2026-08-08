"""Web無料診断「椿の縁視（えにしみ）」。

Threadsのプロフィール/固定投稿のリンクから来た人に、
1) その場で「二人の縁のタイプ」だけ即表示（宿曜の決定的計算＝APIコストゼロ）
2) 鑑定番号（合言葉）を発行して入力内容を保存
3) LINE追加→番号を送ると、ボットが保存済みの内容で本診断を自動返信（line_bot側）
という導線を作る。ページは line_app.py の GET /shindan で配信。

BAN対策の設計: Threads側では返信もDMも一切せず、入口をこのページに集約する。
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from urllib.parse import quote

from . import store
from .config import active_profile
from .diagnosis import _shuku_distance, honmei_shuku

# 番号の有効期限（発行からこの日数を過ぎたらLINE側で受け付けない）
CODE_TTL_DAYS = 7

# 二人の縁のタイプ（27宿サイクルの距離から決定的に判定。同じ二人なら必ず同じ結果）
# 生年月日フラグメント（1940〜2030）と紛れない番号帯を使うため、コードは3000〜9999で発行する
EN_TYPES = [
    (0, {
        "name": "写し鏡の縁", "yomi": "うつしかがみのえん",
        "catch": "似すぎるほど似た二人",
        "desc": "あんたと彼は、根っこの性質がほとんど同じや。分かり合えるのも早いけど、意地の張り方まで同じやから、一度こじれると二人とも引くに引けんくなる。今の膠着は、たぶんそれや。",
    }),
    (2, {
        "name": "庇の縁", "yomi": "ひさしのえん",
        "catch": "守り、守られる縁",
        "desc": "どちらかが自然と相手を守る形になる、居心地のええ縁や。ただな、居心地がええ分だけ「甘え」も出やすい。彼が安心しきって動かんのは、この縁の裏の顔やで。",
    }),
    (5, {
        "name": "棘の縁", "yomi": "とげのえん",
        "catch": "惹かれ合うほど、傷つけ合う",
        "desc": "強烈に惹かれ合うのに、近づきすぎるとお互いを傷つける。ほんで離れたら離れたで、忘れられへん。厄介やけど、それだけ引力の強い縁や。扱い方さえ間違えんかったらな。",
    }),
    (7, {
        "name": "並木の縁", "yomi": "なみきのえん",
        "catch": "同じ歩幅で並んで歩く",
        "desc": "恋人というより相棒に近い、波風の少ない縁や。安心はあるけど、ドキドキが薄れやすい。彼が「居て当たり前」みたいな顔しとるんは、この縁の性質でもあるんよ。",
    }),
    (10, {
        "name": "火花の縁", "yomi": "ひばなのえん",
        "catch": "ぶつかって、育つ",
        "desc": "性質が違いすぎて、ぶつかる時は派手にぶつかる。そのかわり、噛み合うた時の熱は人一倍や。退屈だけは絶対せえへん縁。問題は、今どっちの局面におるかや。",
    }),
    (13, {
        "name": "糸の縁", "yomi": "いとのえん",
        "catch": "切れそうで、切れへん",
        "desc": "持ってるもんが正反対で、お互いに無いもんを埋め合う縁や。理解し合うのに時間はかかる。せやけど、細うて強い糸で繋がっとって、簡単には切れへん。焦りが一番の毒やな。",
    }),
]


def en_type(me_birth: str, him_birth: str) -> dict:
    """二人の縁のタイプを決定的に返す（同じ生年月日の組なら必ず同じタイプ）。"""
    dist = _shuku_distance(honmei_shuku(me_birth), honmei_shuku(him_birth))
    for max_dist, t in EN_TYPES:
        if dist <= max_dist:
            return {**t, "distance": dist}
    return {**EN_TYPES[-1][1], "distance": dist}


# ---------------- 単体の「恋愛タイプ」バッジ（シェア用・本人の生年月日だけで決まる） ----------------
# 悩みを一切出さない“自己紹介バッジ”。MBTI/動物占いのように貼っても恥ずかしくない設計。
# 本人の本命宿(27種)を12タイプに決定的にマッピングする（同じ生年月日なら必ず同じタイプ）。
LOVE_TYPES = {
    "tsukushi_ookami": {"name": "尽くし過ぎ狼", "yomi": "つくしすぎおおかみ", "catch": "好きになったら一直線",
        "line": "惚れたら一直線。健気なんは長所やけど、その全力さ、相手にはちょっと重いで😌"},
    "tsundere": {"name": "ツンデレ女王", "yomi": "つんでれじょおう", "catch": "好きほど、素っ気ない",
        "line": "好きな相手ほどキツう当たってまう。それ、向こうには『嫌われてる』としか伝わってへんで。"},
    "seibo": {"name": "尽くし系聖母", "yomi": "つくしけいせいぼ", "catch": "与えて、つい甘やかす",
        "line": "尽くすんが愛や思てるやろ。せやからあんた、都合よう扱われがちなんや。気づいてる？"},
    "neko": {"name": "きまぐれ猫", "yomi": "きまぐれねこ", "catch": "追うと逃げ、放つと拗ねる",
        "line": "追われたら冷める、放っとかれたら寂しい。あんた自分でもめんどくさい自覚、あるやろ😏"},
    "romantic": {"name": "ロマンチスト脚本家", "yomi": "ろまんちすときゃくほんか", "catch": "頭の中で恋を完成させる",
        "line": "妄想の中で相手はいつも完璧。ほんで現実の相手に勝手にがっかりする。あるあるやろ。"},
    "hunter": {"name": "小悪魔ハンター", "yomi": "こあくまはんたー", "catch": "惚れさせるまでが好き",
        "line": "落とすまでは全力、落とした途端に興味半減。ほんまは追われるより、追いたい人や。"},
    "chototsu": {"name": "猪突猛進タイプ", "yomi": "ちょとつもうしんたいぷ", "catch": "好き＝即・行動",
        "line": "好きになったら止まられへん。勢いは武器やけど、その速さで何回か引かれてきたやろ😅"},
    "bannin": {"name": "慎重すぎる番人", "yomi": "しんちょうすぎるばんにん", "catch": "好きでも、動けん",
        "line": "石橋叩きすぎて渡らへんタイプ。慎重なんはええけど、それで何回チャンス見逃してきた？"},
    "gaman": {"name": "我慢の限界タイプ", "yomi": "がまんのげんかいたいぷ", "catch": "溜めて、溜めて、爆発",
        "line": "言いたいこと飲み込んで、限界で一気に爆発する癖。相手はいっつも『急に⁉︎』ってなってるで。"},
    "kamatte": {"name": "かまってちゃん", "yomi": "かまってちゃん", "catch": "愛は、確かめ続けたい",
        "line": "好き？ねえ好き？を確かめたい人。愛情深いんやけど、その確認、相手にはちょい重い時あるで。"},
    "joou": {"name": "孤高の女王", "yomi": "ここうのじょおう", "catch": "弱み、絶対見せへん",
        "line": "プライド高うて、自分から折れられへん。強がってる間に、ええ縁いくつか逃してへんか？"},
    "hime": {"name": "受け身の姫", "yomi": "うけみのひめ", "catch": "好きって言われるまで動かん",
        "line": "自分からは絶対いかへん、待つ女。奥ゆかしいけど、待ちすぎて他の子に取られる典型やで。"},
}
# 27宿 → 12タイプ（性質メモがある宿は寄せて、残りは重複なく散らして割当）
_LOVE_ASSIGN = {
    "昴宿": "seibo", "畢宿": "tsukushi_ookami", "觜宿": "romantic", "参宿": "chototsu",
    "井宿": "tsundere", "鬼宿": "neko", "柳宿": "hunter", "星宿": "bannin", "張宿": "joou",
    "翼宿": "hunter", "軫宿": "romantic", "角宿": "kamatte", "亢宿": "chototsu", "氐宿": "bannin",
    "房宿": "seibo", "心宿": "gaman", "尾宿": "tsundere", "箕宿": "neko", "斗宿": "gaman",
    "女宿": "hime", "虚宿": "neko", "危宿": "romantic", "室宿": "joou", "壁宿": "bannin",
    "奎宿": "kamatte", "婁宿": "hime", "胃宿": "tsukushi_ookami",
}


def love_type(me_birth: str) -> dict:
    """本人の生年月日だけで決まる『恋愛タイプ』を返す（シェア用バッジ。悩みは出さない）。"""
    key = _LOVE_ASSIGN.get(honmei_shuku(me_birth), "tsukushi_ookami")
    return {"key": key, **LOVE_TYPES[key]}


def _issue_code() -> str:
    """未使用の4桁鑑定番号を発行する（3000〜9999＝生年月日の年と紛れない帯）。"""
    for _ in range(30):
        code = str(random.randint(3000, 9999))
        if not store.get_web_diag(code):
            return code
    raise RuntimeError("鑑定番号の発行に失敗（空きが見つからない）")


def submit(data: dict) -> dict:
    """フォーム送信を受けて、縁タイプ判定＋鑑定番号の発行・保存を行う。"""
    me = str(data.get("me", "")).strip()
    him = str(data.get("him", "")).strip()
    datetime.strptime(me, "%Y-%m-%d")   # 不正な日付はここでValueError
    datetime.strptime(him, "%Y-%m-%d")
    status = str(data.get("status", "")).strip()[:30]
    period = str(data.get("period", "")).strip()[:30]
    t = en_type(me, him)
    love = love_type(me)  # 本人だけの恋愛タイプ（シェア用バッジ）
    code = _issue_code()
    store.add_web_diag(code, me, him, status, period, t["name"])
    # 「診断を実行した人数」の計測（vid=訪問者の匿名ID。同じ人の複数回をユニーク化する）
    try:
        store.add_web_event("submit", str(data.get("vid", ""))[:64])
    except Exception as e:
        print(f"[shindan] submit計測失敗（診断は継続）: {e}")
    # ★2026-08-08（討論の合意施策①）：番号を「覚えて手で打つ」体験を消す。
    #   oaMessage形式は、押すと椿とのトークが開いて本文に「鑑定番号XXXX」が入っとる。
    #   送信を押すだけや。未追加の端末では追加を挟むが、その場合もコピー釦が保険になる。
    basic = str(active_profile().get("line_basic_id", "") or "").strip().lstrip("@")
    oa_url = (f"https://line.me/R/oaMessage/%40{basic}/?{quote('鑑定番号' + code)}"
              if basic else "")
    return {"ok": True, "code": code, "type": t, "love": love,
            "line_url": active_profile().get("line_url", ""),
            "line_oa_url": oa_url}


def redeem(code: str) -> dict | None:
    """LINEで届いた鑑定番号を照合し、有効なら保存済みの入力を返す（line_botから使う）。"""
    row = store.get_web_diag(code)
    if not row:
        return None
    if str(row.get("used") or "0").lower() in ("1", "true"):
        return None
    created = str(row.get("created_at") or "").replace(" ", "T")
    try:
        if created and datetime.fromisoformat(created) < datetime.now() - timedelta(days=CODE_TTL_DAYS):
            return None
    except ValueError:
        pass
    return row


# ---------------- 診断ページ（1ファイル完結・モバイル前提） ----------------

_CAMELLIA = """<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" class="flower">
<circle cx="50" cy="34" r="17" fill="#b3364b"/><circle cx="35" cy="45" r="17" fill="#a52e44"/>
<circle cx="65" cy="45" r="17" fill="#c04057"/><circle cx="41" cy="60" r="17" fill="#b3364b"/>
<circle cx="59" cy="60" r="17" fill="#a52e44"/><circle cx="50" cy="48" r="10" fill="#d9a441"/>
<circle cx="46" cy="45" r="1.8" fill="#f3e3b8"/><circle cx="54" cy="45" r="1.8" fill="#f3e3b8"/>
<circle cx="50" cy="52" r="1.8" fill="#f3e3b8"/><circle cx="45" cy="51" r="1.5" fill="#f3e3b8"/>
<circle cx="55" cy="51" r="1.5" fill="#f3e3b8"/>
<path d="M62 72 Q78 70 84 84 Q68 88 62 76 Z" fill="#3f6b4f"/></svg>"""

PAGE_HTML = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>椿の縁視｜彼の本音、視たろか</title>
<meta name="description" content="二人の生年月日だけで、縁の形と彼の本音を無料で視る。登録不要・30秒。">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#171114; color:#efe6da; font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif JP",serif;
  -webkit-font-smoothing:antialiased; line-height:1.9; }
main { max-width:480px; margin:0 auto; padding:34px 20px 60px; }
.flower { width:64px; display:block; margin:0 auto 14px; }
h1 { text-align:center; font-size:26px; letter-spacing:.3em; font-weight:600; }
.yomi { text-align:center; font-size:11px; letter-spacing:.5em; color:#b08d3e; margin:4px 0 18px; }
.copy { text-align:center; font-size:14px; color:#cdbfae; margin-bottom:30px; }
.copy b { color:#efe6da; font-weight:600; }
fieldset { border:1px solid #3a2a30; border-radius:10px; padding:16px 14px 14px; margin-bottom:18px; background:#1e1519; }
legend { padding:0 8px; font-size:13px; color:#d9a441; letter-spacing:.2em; }
.row { display:flex; gap:8px; }
select { flex:1 1 0; min-width:0; width:100%; appearance:none; background:#241a20 url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="10" height="6"><path d="M0 0l5 6 5-6z" fill="%23b08d3e"/></svg>') no-repeat right 10px center;
  color:#efe6da; border:1px solid #443037; border-radius:8px; padding:12px 22px 12px 8px; font-size:16px; font-family:inherit; }
.pills { display:flex; flex-wrap:wrap; gap:8px; }
.pills label { display:block; }
.pills input { display:none; }
.pills span { display:inline-block; padding:9px 14px; border:1px solid #443037; border-radius:999px;
  font-size:13.5px; color:#cdbfae; cursor:pointer; }
.pills input:checked + span { background:#c04057; border-color:#c04057; color:#fff; }
button { width:100%; padding:16px; margin-top:6px; background:#c04057; color:#fff; border:none; border-radius:10px;
  font-size:17px; letter-spacing:.15em; font-family:inherit; cursor:pointer; }
button:disabled { opacity:.5; }
.err { color:#e88; font-size:13px; text-align:center; margin-top:10px; min-height:1em; }
.note { font-size:11.5px; color:#8d8177; text-align:center; margin-top:14px; }
#result { display:none; }
/* シェア用バッジ（スクショ映え・悩みは出さない自己紹介カード） */
.badge { border:1px solid #d9a441; border-radius:16px; padding:30px 22px 26px; text-align:center;
  background:radial-gradient(120% 90% at 50% 0%, #33232b 0%, #1c1318 70%); margin-bottom:14px; position:relative; overflow:hidden; }
.badge .btag { font-size:11px; letter-spacing:.42em; color:#d9a441; }
.badge .blbl { font-size:12px; letter-spacing:.3em; color:#cdbfae; margin-top:16px; }
.badge .bname { font-size:33px; font-weight:600; letter-spacing:.1em; margin:8px 0 2px; color:#fff; line-height:1.3; }
.badge .byomi { font-size:10.5px; letter-spacing:.35em; color:#8d8177; }
.badge .bcatch { font-size:14px; color:#d9a441; margin:12px 0 14px; }
.badge .bline { font-size:14px; text-align:left; color:#efe6da; line-height:1.95; }
.badge .bsig { font-size:11px; letter-spacing:.3em; color:#8d8177; text-align:right; margin-top:12px; }
.sharelead { text-align:center; font-size:13.5px; color:#cdbfae; margin:0 0 10px; }
.share { display:flex; gap:8px; margin-bottom:26px; }
.share button { flex:1 1 0; padding:12px 4px; margin:0; font-size:13px; letter-spacing:.05em;
  background:#241a20; color:#efe6da; border:1px solid #443037; border-radius:9px; }
.share button.x { background:#111; border-color:#333; }
.share button.th { background:#101010; border-color:#333; }
.share .done { color:#06C755; }
.card { border:1px solid #b08d3e; border-radius:12px; padding:26px 20px; text-align:center;
  background:linear-gradient(160deg,#241a20,#1c1318); margin-bottom:22px; }
.card .lbl { font-size:11px; letter-spacing:.5em; color:#b08d3e; }
.card h2 { font-size:30px; letter-spacing:.18em; margin:10px 0 2px; }
.card .ty { font-size:11px; letter-spacing:.4em; color:#8d8177; margin-bottom:12px; }
.card .catch { font-size:15px; color:#d9a441; margin-bottom:14px; }
.card .desc { font-size:14px; text-align:left; color:#e5dbcd; }
.next { text-align:center; font-size:14.5px; margin-bottom:20px; }
.next b { color:#d9a441; font-weight:600; }
.codebox { border:1px dashed #b08d3e; border-radius:10px; text-align:center; padding:14px; margin-bottom:20px; }
.codebox .lbl { font-size:12px; color:#b08d3e; letter-spacing:.3em; }
.codebox .code { font-size:38px; letter-spacing:.3em; color:#fff; font-weight:600; }
.linebtn { display:block; text-align:center; background:#06C755; color:#fff; text-decoration:none;
  padding:16px; border-radius:10px; font-size:16.5px; letter-spacing:.1em; margin-bottom:14px; }
.step { font-size:13px; color:#cdbfae; text-align:center; }
.copybtn { margin-top:8px; background:none; border:1px solid #b08d3e; color:#d9a441;
  border-radius:8px; padding:6px 18px; font-size:12.5px; letter-spacing:.15em; cursor:pointer; }
footer { text-align:center; font-size:10.5px; letter-spacing:.35em; color:#6d6257; margin-top:44px; }
</style></head><body><main>
""" + _CAMELLIA + """
<h1>椿の縁視</h1>
<div class="yomi">つばきのえにしみ</div>
<p class="copy">彼の本音、視たろか。<br>二人の生年月日だけでええ。<b>登録不要・30秒</b>や。</p>

<form id="f">
  <fieldset><legend>あんたの生年月日</legend>
    <div class="row"><select id="my" required></select><select id="mm" required></select><select id="md" required></select></div>
  </fieldset>
  <fieldset><legend>彼の生年月日</legend>
    <div class="row"><select id="hy" required></select><select id="hm" required></select><select id="hd" required></select></div>
  </fieldset>
  <fieldset><legend>今の状況</legend>
    <div class="pills" id="st"></div>
  </fieldset>
  <fieldset><legend>彼と最後に連絡が取れたのは</legend>
    <div class="pills" id="pe"></div>
  </fieldset>
  <button type="submit" id="go">縁を視る（無料）</button>
  <div class="err" id="err"></div>
  <p class="note">入力した内容は鑑定のためだけに使うで。</p>
</form>

<section id="result">
  <div class="badge" id="badge">
    <div class="btag">#椿の縁視</div>
    <div class="blbl">あんたの恋愛タイプ</div>
    <div class="bname" id="bname"></div>
    <div class="byomi" id="byomi"></div>
    <div class="bcatch" id="bcatch"></div>
    <div class="bline" id="bline"></div>
    <div class="bsig">—— 椿</div>
  </div>
  <p class="sharelead">当たってたら、友達にも当てて回してみ👇</p>
  <div class="share">
    <button type="button" class="x" id="shX">Xでシェア</button>
    <button type="button" class="th" id="shT">スレッズ</button>
    <button type="button" id="shC">リンクをコピー</button>
  </div>

  <div class="card">
    <div class="lbl">二人の縁</div>
    <h2 id="tname"></h2>
    <div class="ty" id="tyomi"></div>
    <div class="catch" id="tcatch"></div>
    <div class="desc" id="tdesc"></div>
  </div>
  <p class="next">縁のカタチは、これで出た。<br>ほな——彼が<b>“今”あんたをどう思てるか</b>。<b>どこまで待てばええか</b>。<b>次の一手を何にするか</b>。<br>そこから先は、<b>LINEの方で視る。</b></p>
  <div class="codebox"><div class="lbl">あんたの鑑定番号</div><div class="code" id="code"></div>
    <button type="button" class="copybtn" id="copyb">番号をコピー</button></div>
  <a class="linebtn" id="lbtn" href="#">LINEで続きを視てもらう</a>
  <p class="step" id="stept">上のボタン押したら、番号は入っとる。<b>送信だけしてな。</b><br>開かん時は、番号をコピーして送ってくれたらええで🌙</p>
</section>

<footer>椿｜彼の本音しか視ん</footer>
</main>
<script>
// 計測（訪問者の匿名ID＋ビーコン。失敗しても画面には影響させない）
const VID=(()=>{try{
  let v=localStorage.getItem("tsubaki_vid");
  if(!v){ v=(crypto.randomUUID?crypto.randomUUID():Date.now()+"-"+Math.random().toString(36).slice(2));
          localStorage.setItem("tsubaki_vid",v); }
  return v;
}catch(_){ return ""; }})();
function track(e){ try{ navigator.sendBeacon("/shindan/track?e="+e+"&vid="+encodeURIComponent(VID)); }catch(_){} }
track("view");
document.getElementById("lbtn").addEventListener("click", ()=>track("line_click"));
// 恋愛タイプ・バッジのシェア導線（友達が自分のタイプを視に来る＝新規流入の複利ループ）
function setupShare(name){
  const url = location.origin + "/shindan";
  const text = "私の恋愛タイプ、【"+name+"】やった…当たりすぎて笑う😇\\nあんたのタイプも30秒で視てみ→ #椿の縁視";
  const full = text + "\\n" + url;
  const X = document.getElementById("shX"), T = document.getElementById("shT"), C = document.getElementById("shC");
  X.onclick = ()=>{ track("share_x");
    window.open("https://twitter.com/intent/tweet?text="+encodeURIComponent(text)+"&url="+encodeURIComponent(url),"_blank"); };
  T.onclick = ()=>{ track("share_threads");
    window.open("https://www.threads.net/intent/post?text="+encodeURIComponent(full),"_blank"); };
  C.onclick = async ()=>{ track("share_copy");
    try{
      if(navigator.share){ await navigator.share({text, url}); return; }
      await navigator.clipboard.writeText(full);
      C.textContent="コピーした！貼ってな"; C.classList.add("done");
      setTimeout(()=>{ C.textContent="リンクをコピー"; C.classList.remove("done"); }, 2200);
    }catch(_){}
  };
}
const STATUS = ["音信不通","既読スルー","急に冷められた","別れ話の後","片思いで進展なし","復縁したい","その他・複雑"];
const PERIOD = ["今日〜昨日（ごく最近）","〜3日","〜2週間","1ヶ月以上","片思い・まだこれから"];
function opts(sel, from, to, suffix, ph){
  sel.add(new Option(ph, ""));
  for(let v=from; from<to ? v<=to : v>=to; v+=(from<to?1:-1)) sel.add(new Option(v+suffix, v));
}
["my","hy"].forEach(id=>opts(document.getElementById(id), 2012, 1950, "年", "年"));
["mm","hm"].forEach(id=>opts(document.getElementById(id), 1, 12, "月", "月"));
["md","hd"].forEach(id=>opts(document.getElementById(id), 1, 31, "日", "日"));
function pills(id, arr, name){
  document.getElementById(id).innerHTML = arr.map(v=>
    `<label><input type="radio" name="${name}" value="${v}"><span>${v}</span></label>`).join("");
}
pills("st", STATUS, "status"); pills("pe", PERIOD, "period");
function bd(p){
  const y=document.getElementById(p+"y").value, m=document.getElementById(p+"m").value, d=document.getElementById(p+"d").value;
  if(!y||!m||!d) return null;
  const dt=new Date(+y, +m-1, +d);
  if(dt.getMonth() !== +m-1) return "bad";
  return `${y}-${String(m).padStart(2,"0")}-${String(d).padStart(2,"0")}`;
}
document.getElementById("f").addEventListener("submit", async (e)=>{
  e.preventDefault();
  const err=document.getElementById("err"); err.textContent="";
  const me=bd("m"), him=bd("h");
  if(!me||!him){ err.textContent="生年月日を選んでな。"; return; }
  if(me==="bad"||him==="bad"){ err.textContent="その日は暦に無いで。日を選び直してな。"; return; }
  const st=document.querySelector('input[name="status"]:checked');
  if(!st){ err.textContent="今の状況をひとつ選んでな。"; return; }
  const pe=document.querySelector('input[name="period"]:checked');
  const go=document.getElementById("go"); go.disabled=true; go.textContent="視てる……";
  try{
    const r=await fetch("/shindan/api",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({me, him, status:st.value, period:pe?pe.value:"", vid:VID})});
    const j=await r.json();
    if(!j.ok) throw new Error(j.error||"failed");
    // 恋愛タイプ・バッジ（シェア用・悩みは出さない）
    if(j.love){
      document.getElementById("bname").textContent="【"+j.love.name+"】";
      document.getElementById("byomi").textContent=j.love.yomi;
      document.getElementById("bcatch").textContent="——"+j.love.catch+"——";
      document.getElementById("bline").textContent=j.love.line;
      setupShare(j.love.name);
    }
    document.getElementById("tname").textContent=j.type.name;
    document.getElementById("tyomi").textContent=j.type.yomi;
    document.getElementById("tcatch").textContent="——"+j.type.catch+"——";
    document.getElementById("tdesc").textContent=j.type.desc;
    document.getElementById("code").textContent=j.code;
    document.getElementById("lbtn").href=j.line_oa_url||j.line_url;
    if(!j.line_oa_url){ document.getElementById("stept").innerHTML=
      "追加したら、この番号だけ送ってな。<br>すぐに“彼の今の本音”を視て返すで🌙"; }
    document.getElementById("copyb").onclick=async()=>{
      const b=document.getElementById("copyb");
      try{ await navigator.clipboard.writeText(j.code); }
      catch(_){ const t=document.createElement("textarea"); t.value=j.code;
        document.body.appendChild(t); t.select();
        try{ document.execCommand("copy"); }catch(__){} document.body.removeChild(t); }
      b.textContent="コピーしたで"; setTimeout(()=>{ b.textContent="番号をコピー"; },2000);
    };
    document.getElementById("f").style.display="none";
    document.getElementById("result").style.display="block";
    window.scrollTo({top:0, behavior:"smooth"});
  }catch(_){
    err.textContent="ごめんな、視るのに失敗したわ。少し待ってもう一回押してみて。";
    go.disabled=false; go.textContent="縁を視る（無料）";
  }
});
</script></body></html>"""


def page_html() -> str:
    return PAGE_HTML
