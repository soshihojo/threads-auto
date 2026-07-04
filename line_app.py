"""LINE Webhookサーバー（椿姉のAI自動返信ボット）。

LINE Messaging APIのWebhookを受けて src/line_bot.py に処理を渡す。
デプロイ: Render等で `uvicorn line_app:app --host 0.0.0.0 --port $PORT`
セットアップ手順は DEPLOY_LINE.md を参照。
"""
from __future__ import annotations

import json

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from src import line_bot, store

app = FastAPI()
store.init_db()


@app.get("/")
def health() -> dict:
    return {"ok": True}


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks) -> str:
    body = await request.body()
    if not line_bot.verify_signature(body, request.headers.get("x-line-signature", "")):
        raise HTTPException(status_code=400, detail="bad signature")
    data = json.loads(body)
    # LINEには即200を返し、生成・返信はバックグラウンドで行う
    #（replyトークンは約1分有効。Claude生成は数十秒以内に収まる）
    for ev in data.get("events", []):
        background_tasks.add_task(line_bot.handle_event, ev)
    return "OK"
