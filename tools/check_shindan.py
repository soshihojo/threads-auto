# -*- coding: utf-8 -*-
"""診断ページのJSを、実際に走らせて確かめる。

★★★2026-08-31 新設。理由を残す。
　const SRC を足す時に、上の const VID=(()=>{ の【一行目】にだけマッチする
　正規表現を使うてしもて、IIFE の内側に入った。
　★構文としては通る。せやから node --check では見つからん。
　★★けど SRC は関数スコープに閉じるんで、下の fetch の行で ReferenceError。
　　try/catch に拾われて、画面には「ごめんな、視るのに失敗したわ」だけが出る。
　★★★実害：LINE追加が丸ごと止まった。

　せやから【構文】やのうて【実際に走らせて、使う変数が届くか】を見る。
　使い方： .venv/bin/python tools/check_shindan.py
"""
from __future__ import annotations
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import web_diag  # noqa: E402

# 送信の本文で使う変数。ここに届かんかったら診断が丸ごと止まる。
MUST_REACH = ("VID", "SRC")

# ブラウザの代わり。JSが触るもんだけ、最低限を用意する。
STUBS = """
globalThis.location = { search: "?a=b" };
globalThis.localStorage = { _d:{}, getItem(k){return this._d[k]||null;},
                            setItem(k,v){this._d[k]=v;} };
globalThis.crypto = { randomUUID: () => "test-uuid" };
globalThis.navigator = { sendBeacon: () => true, clipboard: { writeText: async()=>{} } };
globalThis.Option = function(text, value){ return { text, value }; };
const _el = () => ({ textContent:"", innerHTML:"", href:"", value:"", disabled:false,
                     checked:false, style:{}, onclick:null, options:[],
                     appendChild(){}, removeChild(){}, select(){}, add(){}, remove(){},
                     addEventListener(){}, querySelector:()=>null, querySelectorAll:()=>[],
                     insertAdjacentHTML(){}, setAttribute(){}, focus(){} });
globalThis.document = { getElementById:_el, querySelector:()=>null, querySelectorAll:()=>[],
                        createElement:_el, body:{ appendChild(){}, removeChild(){} },
                        addEventListener(){} };
globalThis.window = { scrollTo(){}, open(){} };
globalThis.fetch = async () => ({ json: async () => ({ ok:true }) });
"""


def main() -> int:
    html = web_diag.page_html()
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    if not blocks:
        print("❌ <script> が見つからん。ページの作りが変わったか？")
        return 1
    js = "\n".join(blocks)

    if not shutil_which("node"):
        print("⚠ node が無いんで実走はできん。トップレベル宣言の目視だけする")
        ok = True
        for name in MUST_REACH:
            at_top = bool(re.search(rf"^(?:const|let|var)\s+{name}\b", js, re.M))
            print(f"   {'✅' if at_top else '❌'} {name} がトップレベルで宣言されとる")
            ok &= at_top
        return 0 if ok else 1

    # 実走：スタブを積んでからページのJSを流し、使う変数に届くか確かめる
    checks = ";".join(
        f'if(typeof {n}==="undefined") {{ bad.push("{n}"); }}' for n in MUST_REACH
    )
    script = (STUBS + "\n" + js + "\n"
              + f'const bad=[]; {checks}\n'
              + 'if(bad.length){ console.log("NG:"+bad.join(",")); process.exit(3); }\n'
              + 'console.log("OK:"+JSON.stringify({'
              + ",".join(f'{n}:String(typeof {n}!=="undefined"?{n}:"")' for n in MUST_REACH)
              + '}));')
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
        path = f.name
    try:
        r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
    finally:
        Path(path).unlink(missing_ok=True)

    out = (r.stdout or "").strip()
    if r.returncode == 3:
        missing = out.replace("NG:", "")
        print(f"❌ 送信の本文で使う変数が届いてへん：{missing}")
        print("   ★これが出とる時、診断ボタンは「ごめんな、視るのに失敗したわ」で止まる。")
        print("   ★関数（IIFE）の中に入れてへんか、トップレベルに出すこと。")
        return 1
    if r.returncode != 0:
        print(f"❌ JSが走らんかった（構文エラーか、スタブ不足）\n{(r.stderr or '')[:800]}")
        return 1
    vals = json.loads(out.replace("OK:", "")) if out.startswith("OK:") else {}
    print("✅ 診断ページのJSは走る。送信で使う変数も届いとる")
    for k, v in vals.items():
        print(f"   {k} = {v!r}")
    if vals.get("SRC") != "b":
        print(f"   ⚠ ?a=b で来たのに SRC が {vals.get('SRC')!r}。流入元が記録されん")
        return 1
    return 0


def shutil_which(cmd: str):
    import shutil
    return shutil.which(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
