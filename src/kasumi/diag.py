"""かすみの診断ページ「推しとあなたの縁診断」。

GET  /kasumi/shindan       … page_html()
POST /kasumi/shindan/api   … submit()：縁タイプを出して4桁の番号を発行・保存する
LINEで番号が届いたら bot.py が redeem() で照合する。

結果ページは手書きの文面（texts.py）だけを出す。生成（LLM）は使わない。
"""
from __future__ import annotations

import random
import re
from datetime import datetime, timedelta
from urllib.parse import quote

from ..config import env
from . import store, texts
from .en import relation

CODE_TTL_DAYS = 7
LINE_BASIC_ID = (env("KASUMI_LINE_BASIC_ID") or "751dznkf").lstrip("@")
LINE_URL = env("KASUMI_LINE_URL") or "https://lin.ee/XyrCyiZ"
CODE_PREFIX = "縁の番号"


def _issue_code() -> str:
    # 3000〜9999：生年月日の「年」と紛れない帯（椿と同じ考え方）
    for _ in range(50):
        code = str(random.randint(3000, 9999))
        if not store.code_exists(code):
            return code
    raise RuntimeError("番号の発行に失敗（空きが見つからない）")


def submit(data: dict) -> dict:
    me = str(data.get("me", "")).strip()
    oshi = str(data.get("oshi", "")).strip()
    datetime.strptime(me, "%Y-%m-%d")     # 不正な日付は ValueError
    datetime.strptime(oshi, "%Y-%m-%d")
    state = str(data.get("state", "")).strip()
    worry = str(data.get("worry", "")).strip()
    if state not in texts.STATES or worry not in texts.WORRIES:
        raise ValueError("選択肢にない値")
    rel = relation(me, oshi)
    code = _issue_code()
    src = re.sub(r"[^0-9a-zA-Z_-]", "", str(data.get("src", "")))[:16]
    store.add_diag(code, me, oshi, state, worry, rel, source=src)
    try:
        store.add_event("submit", str(data.get("vid", "")))
    except Exception as e:
        print(f"[kasumi] submit計測失敗（診断は継続）: {e}")
    t = texts.EN_TYPES[rel]
    return {"ok": True, "code": code, "name": t["name"], "iiate": t["iiate"],
            "line_url": LINE_URL,
            "line_oa_url": f"https://line.me/R/oaMessage/%40{LINE_BASIC_ID}/?{quote(CODE_PREFIX + code)}"}


def redeem(code: str) -> dict | None:
    """有効な番号なら診断の行を返す。使用済み・期限切れ・無い番号は None。"""
    row = store.get_diag(code)
    if not row or str(row.get("used") or "0") in ("1", "true", "TRUE"):
        return None
    try:
        created = datetime.fromisoformat(str(row.get("created_at") or "").replace(" ", "T"))
        if created.tzinfo:
            created = created.replace(tzinfo=None)
        if created < datetime.now() - timedelta(days=CODE_TTL_DAYS):
            return None
    except ValueError:
        pass
    return row


# ---------------- ページ ----------------
_FLOWER = """<svg class="flower" viewBox="0 0 120 60" aria-hidden="true">
<g fill="#fff" stroke="#c9bde3" stroke-width="1.2">
<circle cx="20" cy="30" r="6"/><circle cx="38" cy="18" r="5"/><circle cx="52" cy="34" r="6.5"/>
<circle cx="68" cy="16" r="5"/><circle cx="82" cy="32" r="6"/><circle cx="100" cy="20" r="5"/></g>
<g fill="#e7dcf5"><circle cx="20" cy="30" r="2"/><circle cx="38" cy="18" r="1.6"/><circle cx="52" cy="34" r="2.2"/>
<circle cx="68" cy="16" r="1.6"/><circle cx="82" cy="32" r="2"/><circle cx="100" cy="20" r="1.6"/></g></svg>"""

PAGE_HTML = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>推しとあなたの縁診断｜かすみ</title>
<meta name="description" content="あなたと推しの生年月日から、ふたりの縁の形を読みます。30秒・無料。">
<style>
:root{--bg:#faf8fc;--card:#fff;--ink:#3d3550;--sub:#7a7190;--accent:#8e7cc3;--soft:#efe9f8;--line:#06c755;--err:#c0455b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif;line-height:1.75}
main{max-width:460px;margin:0 auto;padding:24px 16px 48px}
.flower{display:block;width:120px;margin:0 auto 4px}
h1{font-size:22px;text-align:center;margin:4px 0 6px;letter-spacing:.04em}
.lead{text-align:center;color:var(--sub);font-size:14px;margin:0 0 20px}
.card{background:var(--card);border-radius:16px;padding:20px 18px;box-shadow:0 2px 12px rgba(80,60,120,.06);margin-bottom:16px}
label.q{display:block;font-weight:600;font-size:15px;margin:18px 0 8px}
label.q:first-child{margin-top:0}
.hint{font-size:12px;color:var(--sub);margin:-4px 0 8px}
.dates{display:flex;gap:6px}
select{flex:1;font-size:16px;padding:10px 6px;border:1px solid #d9d0ea;border-radius:10px;background:#fff;color:var(--ink)}
.pills{display:flex;flex-wrap:wrap;gap:8px}
.pills label{cursor:pointer}
.pills input{display:none}
.pills span{display:inline-block;padding:8px 12px;border:1px solid #d9d0ea;border-radius:999px;font-size:14px;background:#fff}
.pills input:checked+span{background:var(--accent);border-color:var(--accent);color:#fff}
button,.btn{display:block;width:100%;border:0;border-radius:999px;padding:15px;font-size:17px;font-weight:700;text-align:center;text-decoration:none;cursor:pointer}
#go{background:var(--accent);color:#fff;margin-top:22px}
#go:disabled{opacity:.6}
.err{color:var(--err);font-size:14px;min-height:1.4em;margin-top:8px}
.note{font-size:12px;color:var(--sub);text-align:center;margin-top:10px}
.note a{color:var(--sub)}
#result{display:none}
.rname{text-align:center;font-size:14px;color:var(--sub);margin:0}
.ename{text-align:center;font-size:26px;font-weight:700;margin:2px 0 12px;color:var(--accent)}
.iiate{white-space:pre-line;font-size:15px}
.next ul{padding-left:1.2em;margin:8px 0 16px}
.lbtn{background:var(--line);color:#fff}
.codebox{text-align:center;margin-top:14px;font-size:13px;color:var(--sub)}
.code{font-size:30px;font-weight:700;letter-spacing:.2em;color:var(--ink);margin:4px 0}
#copyb{background:var(--soft);color:var(--ink);font-size:14px;padding:10px;width:auto;display:inline-block;padding:8px 18px}
</style></head><body><main>
__FLOWER__
<h1>推しとあなたの縁診断</h1>
<p class="lead">あなたと推しの生年月日から、<br>ふたりの縁の形を読みます。30秒で終わります。</p>

<form id="f" class="card" novalidate>
  <label class="q">あなたの生年月日</label>
  <div class="dates"><select id="my"></select><select id="mm"></select><select id="md"></select></div>
  <label class="q">推しの生年月日</label>
  <p class="hint">推しが何人もいるなら、いちばんの推しを。キャラクターなど年が決まっていない推しは、公式の誕生日と、あなたが推しと出会った年を入れてください。</p>
  <div class="dates"><select id="oy"></select><select id="om"></select><select id="od"></select></div>
  <label class="q">推し活のいまの状態は？</label>
  <div class="pills" id="st"></div>
  <label class="q">いま一番気になっていることは？</label>
  <div class="pills" id="wo"></div>
  <button id="go" type="submit">縁を読む</button>
  <div class="err" id="err"></div>
  <p class="note">入力した内容は、結果をお届けするためだけに使います。<br><a href="/kasumi/privacy">プライバシーポリシー</a></p>
</form>

<section id="result">
  <div class="card">
    <p class="rname">あなたと推しは</p>
    <p class="ename" id="ename"></p>
    <p class="iiate" id="iiate"></p>
  </div>
  <div class="card next">
    LINEでは、ここから先を詳しく読んでお届けします。
    <ul><li>推しとあなたの縁の、詳しい読み</li><li>推しの生まれ月から見える、推しの素顔</li><li>あなたの推し方のくせと、疲れやすいところのほどき方</li></ul>
    <a class="btn lbtn" id="lbtn" href="#">LINEで続きを受け取る（無料）</a>
    <div class="codebox">
      <p id="stept">ボタンを押すと、番号が入った状態でトークが開きます。そのまま送信してください。<br>開かないときは、この番号をLINEで送ってください。</p>
      <p class="code" id="code"></p>
      <button id="copyb" type="button">番号をコピー</button>
    </div>
  </div>
</section>
</main>
<script>
const STATES=__STATES__, WORRIES=__WORRIES__;
const VID=(()=>{try{let v=localStorage.getItem("kvid");if(!v){v=Math.random().toString(36).slice(2)+Date.now().toString(36);localStorage.setItem("kvid",v);}return v;}catch(_){return "";}})();
const SRC=(()=>{try{return new URLSearchParams(location.search).get("a")||"";}catch(_){return "";}})();
function track(e){try{navigator.sendBeacon("/kasumi/shindan/track?e="+e+"&vid="+encodeURIComponent(VID));}catch(_){}}
track("view");
const IS_APP=/iPhone|iPad|iPod|Android/i.test(navigator.userAgent||"");
if(!IS_APP) track("view_pc");
function opts(sel,from,to,suf,ph){sel.add(new Option(ph,""));for(let v=from;from<to?v<=to:v>=to;v+=(from<to?1:-1))sel.add(new Option(v+suf,v));}
opts(document.getElementById("my"),2015,1935,"年","年");
opts(document.getElementById("oy"),2015,1930,"年","年");
["mm","om"].forEach(id=>opts(document.getElementById(id),1,12,"月","月"));
["md","od"].forEach(id=>opts(document.getElementById(id),1,31,"日","日"));
function pills(id,arr,name){document.getElementById(id).innerHTML=arr.map(v=>`<label><input type="radio" name="${name}" value="${v}"><span>${v}</span></label>`).join("");}
pills("st",STATES,"state"); pills("wo",WORRIES,"worry");
function bd(p){const y=document.getElementById(p+"y").value,m=document.getElementById(p+"m").value,d=document.getElementById(p+"d").value;
  if(!y||!m||!d)return null;const dt=new Date(+y,+m-1,+d);if(dt.getMonth()!==+m-1)return "bad";
  return `${y}-${String(m).padStart(2,"0")}-${String(d).padStart(2,"0")}`;}
document.getElementById("lbtn").addEventListener("click",()=>{track("line_click");
  try{const c=document.getElementById("code").textContent;if(c&&navigator.clipboard)navigator.clipboard.writeText(c).catch(()=>{});}catch(_){}});
document.getElementById("f").addEventListener("submit",async e=>{
  e.preventDefault();const err=document.getElementById("err");err.textContent="";
  const me=bd("m"),oshi=bd("o");
  if(!me||!oshi){err.textContent="生年月日を選んでください。";return;}
  if(me==="bad"||oshi==="bad"){err.textContent="存在しない日付です。日を選び直してください。";return;}
  const st=document.querySelector('input[name="state"]:checked'),wo=document.querySelector('input[name="worry"]:checked');
  if(!st||!wo){err.textContent="いまの状態と、気になっていることを選んでください。";return;}
  const go=document.getElementById("go");go.disabled=true;go.textContent="読んでいます…";
  try{
    const r=await fetch("/kasumi/shindan/api",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({me,oshi,state:st.value,worry:wo.value,vid:VID,src:SRC})});
    const j=await r.json();if(!j.ok)throw new Error("failed");
    document.getElementById("ename").textContent=j.name;
    document.getElementById("iiate").textContent=j.iiate;
    document.getElementById("code").textContent=j.code;
    const useOA=IS_APP&&j.line_oa_url;
    document.getElementById("lbtn").href=useOA?j.line_oa_url:j.line_url;
    if(!useOA)document.getElementById("stept").innerHTML="LINEで友だち追加したら、この番号を送ってください。<br>推しとあなたの縁を、詳しく読んでお返しします。";
    document.getElementById("copyb").onclick=async()=>{const b=document.getElementById("copyb");
      try{await navigator.clipboard.writeText(j.code);}catch(_){}
      b.textContent="コピーしました";setTimeout(()=>{b.textContent="番号をコピー";},2000);};
    document.getElementById("f").style.display="none";document.getElementById("result").style.display="block";
    window.scrollTo({top:0,behavior:"smooth"});
  }catch(_){err.textContent="ごめんなさい、うまく読めませんでした。少し待ってから、もう一度押してください。";go.disabled=false;go.textContent="縁を読む";}
});
</script></body></html>"""


def page_html() -> str:
    import json
    return (PAGE_HTML.replace("__FLOWER__", _FLOWER)
            .replace("__STATES__", json.dumps(texts.STATES, ensure_ascii=False))
            .replace("__WORRIES__", json.dumps(list(texts.WORRIES), ensure_ascii=False)))


PRIVACY_HTML = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>プライバシーポリシー｜かすみ</title>
<style>body{margin:0;background:#faf8fc;color:#3d3550;font-family:-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif;line-height:1.8}
main{max-width:640px;margin:0 auto;padding:24px 16px 48px}h1{font-size:20px}h2{font-size:16px;margin-top:24px}</style></head><body><main>
<h1>プライバシーポリシー</h1>
<p>かすみ | 推し活占い（以下「当サービス」）は、ご利用者さまの情報を、次のとおり取り扱います。</p>
<h2>1. お預かりする情報</h2>
<p>・診断で入力いただいた、ご本人と推しの生年月日、選択いただいた回答<br>・LINEでお送りいただいたメッセージと、ボタンでの回答<br>・LINEの表示名と、LINEが発行するユーザー識別子</p>
<h2>2. 使う目的</h2>
<p>・診断結果と鑑定をお届けするため<br>・LINEでのお便りをお届けするため<br>・個人が分からない形に集計し、サービスをよくするため</p>
<h2>3. 第三者への提供</h2>
<p>法令にもとづく場合を除き、ご本人の同意なく第三者に提供しません。</p>
<h2>4. 外部サービスの利用</h2>
<p>診断・LINEの運営のため、サーバーやデータ保存、自動応答（AIを含む）などの外部サービスを利用しています。</p>
<h2>5. 削除のご依頼</h2>
<p>LINEで「データを削除してほしい」とお送りください。確認のうえ削除します。</p>
<h2>6. お問い合わせ</h2>
<p>このLINEアカウントに、メッセージでお問い合わせください。</p>
<h2>7. 改定</h2>
<p>内容を変えるときは、このページでお知らせします。</p>
<p>制定日：2026年10月22日<br>事業者：かすみ事務局</p>
</main></body></html>"""


def privacy_html() -> str:
    return PRIVACY_HTML
