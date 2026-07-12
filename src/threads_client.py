"""Threads(Meta) Graph API クライアント。

公式エンドポイント（無料）を薄くラップする。
- 投稿（テキスト/画像/カルーセル、位置タグ・geo-gating対応）
- 自分の投稿一覧・返信取得・返信送信・返信を隠す
- インサイト取得
- 長期トークンの自動リフレッシュ
- 投稿/返信の残り枠（publishing limit）取得

参考: https://developers.facebook.com/docs/threads
"""
from __future__ import annotations

import re
import time
from typing import Any

import requests

BASE = "https://graph.threads.net/v1.0"
ROOT_BASE = "https://graph.threads.net"  # token系はバージョン無し


class ThreadsError(RuntimeError):
    pass


class ThreadsClient:
    def __init__(self, access_token: str, user_id: str | None = None, *, timeout: int = 30):
        if not access_token:
            raise ThreadsError("THREADS_ACCESS_TOKEN が空です。")
        self.token = access_token
        self.timeout = timeout
        self._user_id = user_id or None

    # ---------- 低レベル ----------
    def _request(self, method: str, url: str, params: dict | None = None, *, retries: int = 3) -> dict:
        params = dict(params or {})
        params.setdefault("access_token", self.token)
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                resp = requests.request(method, url, params=params, timeout=self.timeout)
            except requests.RequestException as e:  # ネットワーク系は再試行
                last_exc = e
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 429:  # レート超過
                time.sleep(5 * (attempt + 1))
                last_exc = ThreadsError("429 rate limited")
                continue
            if not resp.ok:
                # Meta のエラーは JSON で error.message を返す
                try:
                    msg = resp.json().get("error", {}).get("message", resp.text)
                except Exception:
                    msg = resp.text
                raise ThreadsError(f"{method} {url} -> {resp.status_code}: {msg}")
            return resp.json() if resp.content else {}
        raise ThreadsError(f"リクエスト失敗（{retries}回）: {last_exc}")

    def _get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", f"{BASE}/{path.lstrip('/')}", params)

    def _post(self, path: str, params: dict | None = None) -> dict:
        return self._request("POST", f"{BASE}/{path.lstrip('/')}", params)

    # ---------- アカウント ----------
    @property
    def user_id(self) -> str:
        if not self._user_id:
            me = self._get("me", {"fields": "id,username"})
            self._user_id = me["id"]
        return self._user_id

    def me(self) -> dict:
        return self._get("me", {"fields": "id,username,threads_profile_picture_url,threads_biography"})

    # ---------- トークン ----------
    def refresh_long_lived_token(self) -> dict:
        """長期トークンを更新（24h経過後ならいつでも可、60日延長）。
        返り値の access_token を保存して次回以降使う。"""
        url = f"{ROOT_BASE}/refresh_access_token"
        data = self._request(
            "GET", url, {"grant_type": "th_refresh_token", "access_token": self.token}
        )
        if data.get("access_token"):
            self.token = data["access_token"]
        return data

    # ---------- 残り枠 ----------
    def publishing_limit(self) -> dict:
        """投稿/返信の24h残り枠。quota_usage(投稿), reply_quota_usage(返信) など。"""
        return self._get(
            f"{self.user_id}/threads_publishing_limit",
            {"fields": "quota_usage,config,reply_quota_usage,delete_quota_usage,location_search_quota_usage"},
        )

    # ---------- 投稿 ----------
    def _create_container(self, **params) -> str:
        params = {k: v for k, v in params.items() if v is not None and v != ""}
        res = self._post(f"{self.user_id}/threads", params)
        cid = res.get("id")
        if not cid:
            raise ThreadsError(f"creation container が作れませんでした: {res}")
        return cid

    def _publish_container(self, creation_id: str) -> str:
        # コンテナ作成直後は処理中のことがあるため少し待つ
        time.sleep(2)
        res = self._post(f"{self.user_id}/threads_publish", {"creation_id": creation_id})
        mid = res.get("id")
        if not mid:
            raise ThreadsError(f"publish に失敗: {res}")
        return mid

    def publish_text(
        self,
        text: str,
        *,
        location_id: str | None = None,
        reply_control: str | None = None,  # everyone / accounts_you_follow / mentioned_only
        allowlisted_country_codes: list[str] | None = None,  # geo-gating（適格アカウントのみ）
    ) -> str:
        """テキスト投稿を作成して公開。公開された media_id を返す。"""
        cid = self._create_container(
            media_type="TEXT",
            text=text,
            location_id=location_id,
            reply_control=reply_control,
            allowlisted_country_codes=",".join(allowlisted_country_codes) if allowlisted_country_codes else None,
        )
        return self._publish_container(cid)

    def publish_image(self, text: str, image_url: str, *, location_id: str | None = None) -> str:
        cid = self._create_container(media_type="IMAGE", text=text, image_url=image_url, location_id=location_id)
        return self._publish_container(cid)

    def publish_carousel(self, text: str, image_urls: list[str], *, location_id: str | None = None) -> str:
        children = []
        for url in image_urls:
            c = self._create_container(media_type="IMAGE", image_url=url, is_carousel_item="true")
            children.append(c)
        cid = self._create_container(
            media_type="CAROUSEL", text=text, children=",".join(children), location_id=location_id
        )
        return self._publish_container(cid)

    # ---------- 自分の投稿 ----------
    def my_threads(self, limit: int = 25) -> list[dict]:
        res = self._get(
            f"{self.user_id}/threads",
            {"fields": "id,text,timestamp,permalink,media_type", "limit": limit},
        )
        return res.get("data", [])

    # ---------- 返信 ----------
    def replies(self, media_id: str, *, top_level_only: bool = True) -> list[dict]:
        """ある投稿への返信を取得。"""
        edge = "replies" if top_level_only else "conversation"
        res = self._get(
            f"{media_id}/{edge}",
            {"fields": "id,text,username,timestamp,replied_to,root_post,has_replies,hide_status"},
        )
        return res.get("data", [])

    SPLIT_MARK = "===続き==="

    def publish_thread(self, text: str, *, location_id: str | None = None,
                       part_delay: int = 75) -> str:
        """「===続き===」区切りの分割投稿をツリー（自分への返信の連なり）として順に公開する。
        区切りが無ければ通常の単発投稿。先頭の media_id を返す。
        1本目がフィードに流れ、タップした人が続きを読む構造（分割でクリックを誘発する型）。"""
        parts = [p.strip() for p in re.split(r"\n?===続き===\n?", text) if p.strip()]
        first = self.publish_text(parts[0], location_id=location_id)
        prev = first
        for part in parts[1:]:
            time.sleep(part_delay)
            prev = self.reply_to(prev, part)
        return first

    def reply_to(self, media_id: str, text: str) -> str:
        """指定投稿/返信へ返信する。新しい返信の media_id を返す。"""
        cid = self._create_container(media_type="TEXT", text=text, reply_to_id=media_id)
        return self._publish_container(cid)

    def hide_reply(self, reply_id: str, hide: bool = True) -> dict:
        return self._post(f"{reply_id}/manage_reply", {"hide": "true" if hide else "false"})

    # ---------- インサイト ----------
    def media_insights(self, media_id: str) -> dict:
        res = self._get(
            f"{media_id}/insights",
            {"metric": "views,likes,replies,reposts,quotes,shares"},
        )
        out: dict[str, int] = {}
        for item in res.get("data", []):
            name = item.get("name")
            vals = item.get("values") or [{}]
            out[name] = vals[0].get("value", 0)
        return out

    # ---------- 位置タグ ----------
    def location_search(self, query: str) -> list[dict]:
        """位置タグ用のロケーション候補を検索。先頭候補の id を投稿の location_id に使う。"""
        try:
            res = self._get("location_search", {"q": query, "fields": "id,name,address,city,country"})
            return res.get("data", [])
        except ThreadsError:
            return []  # 検索不可・権限不足でも投稿自体は止めない

    def first_location_id(self, query: str) -> str | None:
        if not query:
            return None
        cands = self.location_search(query)
        return cands[0]["id"] if cands else None
