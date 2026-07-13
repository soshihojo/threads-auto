"""LINE Webhookサーバー（椿のAI自動返信ボット）。

LINE Messaging APIのWebhookを受けて src/line_bot.py に処理を渡す。
デプロイ: Render等で `uvicorn line_app:app --host 0.0.0.0 --port $PORT`
セットアップ手順は DEPLOY_LINE.md を参照。

エルメ等の外部ツールと共存する場合:
LINEのWebhook URLは1つしか設定できないため、このサーバーを受け口にして、
環境変数 FORWARD_WEBHOOK_URL に外部ツールのWebhook URLを設定すると、
受信イベントを署名ごとそのまま転送する（両方が動く）。未設定なら転送しない。
"""
from __future__ import annotations

import asyncio
import json

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src import line_bot, store, web_diag
from src.config import env

app = FastAPI()
store.init_db()

# 未返信スイープ：生成失敗等で返信が落ちた会話を10分ごとに拾い直す安全網
SWEEP_INTERVAL_SEC = int(env("LINE_SWEEP_INTERVAL_SEC") or "300")


@app.on_event("startup")
async def _start_sweeper() -> None:
    async def _loop() -> None:
        loop = asyncio.get_running_loop()
        # 初回は起動60秒後に実行：デプロイの再起動で処理中の返信が切られた場合、
        # 旧インスタンスの取りこぼしを新インスタンスがすぐ拾えるようにする
        #（スイープ自体はmin_age=10分より新しい会話には触らないので二重返信はしない）
        delay = 60
        while True:
            await asyncio.sleep(delay)
            delay = SWEEP_INTERVAL_SEC
            try:
                n = await loop.run_in_executor(None, line_bot.sweep_unanswered)
                if n:
                    print(f"[sweep] {n}件の未返信に対応した")
            except Exception as e:
                print(f"[sweep] 実行失敗（次周期で再試行）: {e}")

    if SWEEP_INTERVAL_SEC > 0:
        asyncio.create_task(_loop())


def _forward(body: bytes, signature: str) -> None:
    """受信したWebhookを外部ツール（エルメ等）へそのまま転送する。"""
    url = env("FORWARD_WEBHOOK_URL")
    if not url:
        return
    try:
        requests.post(
            url,
            data=body,
            headers={"Content-Type": "application/json", "X-Line-Signature": signature},
            timeout=10,
        )
    except requests.RequestException as e:
        print(f"[webhook] 転送失敗（処理は継続）: {e}")


@app.get("/")
def health() -> dict:
    # rev: Renderが自動設定するデプロイ中コミット（デプロイが反映されたかの確認用）
    return {"ok": True, "rev": (env("RENDER_GIT_COMMIT") or "")[:7]}


@app.get("/shindan", response_class=HTMLResponse)
def shindan_page() -> str:
    """Web無料診断「椿の縁視」（Threadsのプロフィール/固定投稿からの入口）。"""
    return web_diag.page_html()


@app.post("/shindan/track")
def shindan_track(e: str, background_tasks: BackgroundTasks, vid: str = "") -> dict:
    """診断ページの計測ビーコン（view=表示 / line_click=LINEボタン押下。vid=訪問者の匿名ID）。"""
    if e in ("view", "line_click"):
        background_tasks.add_task(store.add_web_event, e, vid[:64])
    return {"ok": True}


@app.post("/shindan/api")
async def shindan_api(request: Request) -> JSONResponse:
    try:
        data = await request.json()
        return JSONResponse(web_diag.submit(data))
    except Exception as e:
        print(f"[shindan] 受付失敗: {e}")
        return JSONResponse({"ok": False, "error": "invalid input"}, status_code=400)


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks) -> str:
    body = await request.body()
    signature = request.headers.get("x-line-signature", "")
    if not line_bot.verify_signature(body, signature):
        raise HTTPException(status_code=400, detail="bad signature")
    # エルメ等への転送（FORWARD_WEBHOOK_URL設定時のみ）
    background_tasks.add_task(_forward, body, signature)
    data = json.loads(body)
    # LINEには即200を返し、生成・返信はバックグラウンドで行う
    #（replyトークンは約1分有効。Claude生成は数十秒以内に収まる）
    for ev in data.get("events", []):
        background_tasks.add_task(line_bot.handle_event, ev)
    return "OK"
