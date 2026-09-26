"""Threads API（graph.threads.net）の最小限のクライアント。

使うのは「テキスト投稿」「自分の投稿一覧」「投稿ごとの数字」「フォロワー数」「トークン延長」だけ。
標準ライブラリだけで動く。トークンは環境変数 THREADS_ACCESS_TOKEN、ユーザーIDは THREADS_USER_ID から読む。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://graph.threads.net/v1.0"
REFRESH_URL = "https://graph.threads.net/refresh_access_token"
MAX_TEXT_LENGTH = 500  # Threads のテキスト投稿の上限（文字数）
POST_METRICS = ("views", "likes", "replies", "reposts", "quotes", "shares")


class ThreadsError(RuntimeError):
    pass


def _default_transport(method: str, url: str, params: dict) -> dict:
    data = None
    if method == "GET":
        url = f"{url}?{urllib.parse.urlencode(params)}"
    else:
        data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            message = json.loads(body)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = body[:300]
        raise ThreadsError(f"Threads API {e.code}: {message}") from None


class ThreadsClient:
    def __init__(self, token: str, user_id: str, transport=_default_transport, sleep=time.sleep):
        if not token or not user_id:
            raise ThreadsError("THREADS_ACCESS_TOKEN と THREADS_USER_ID を設定してください")
        self.token = token
        self.user_id = user_id
        self._transport = transport
        self._sleep = sleep

    @classmethod
    def from_env(cls) -> "ThreadsClient":
        return cls(os.environ.get("THREADS_ACCESS_TOKEN", ""), os.environ.get("THREADS_USER_ID", ""))

    def _call(self, method: str, path: str, **params) -> dict:
        params["access_token"] = self.token
        return self._transport(method, f"{BASE_URL}/{path}", params)

    def publish_text(self, text: str) -> str:
        """テキストを投稿して、投稿IDを返す。取り消せないので呼ぶ側で必ず確認してから使う。"""
        if len(text) > MAX_TEXT_LENGTH:
            raise ThreadsError(f"本文が{len(text)}文字あります（上限{MAX_TEXT_LENGTH}文字）")
        container = self._call("POST", f"{self.user_id}/threads", media_type="TEXT", text=text)
        # Meta の推奨どおり、コンテナ作成から公開まで少し待つ
        self._sleep(5)
        published = self._call("POST", f"{self.user_id}/threads_publish", creation_id=container["id"])
        return published["id"]

    def permalink(self, media_id: str) -> str:
        return self._call("GET", media_id, fields="permalink").get("permalink", "")

    def list_threads(self, since: int | None = None, limit: int = 50) -> list[dict]:
        """自分の投稿（新しい順）。since は UNIX 秒。"""
        params = {"fields": "id,text,timestamp,media_type,permalink", "limit": limit}
        if since:
            params["since"] = since
        return self._call("GET", f"{self.user_id}/threads", **params).get("data", [])

    def post_insights(self, media_id: str) -> dict[str, int]:
        res = self._call("GET", f"{media_id}/insights", metric=",".join(POST_METRICS))
        return {m["name"]: m["values"][0]["value"] for m in res.get("data", []) if m.get("values")}

    def followers_count(self) -> int:
        res = self._call("GET", f"{self.user_id}/threads_insights", metric="followers_count")
        for m in res.get("data", []):
            if m.get("name") == "followers_count":
                return m["total_value"]["value"]
        raise ThreadsError("followers_count が返ってきませんでした")

    def refresh_token(self) -> dict:
        """長期トークン（60日）を延長する。返り値の access_token を Secrets に入れ直す。"""
        return self._transport("GET", REFRESH_URL, {"grant_type": "th_refresh_token", "access_token": self.token})
