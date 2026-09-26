#!/usr/bin/env python3
"""Threads から直近の投稿の数字を取ってきて、分析メモ（analytics/posts.csv）に書き込む。

使い方:
    python3 tools/fetch_insights.py            # 直近14日の投稿を更新
    python3 tools/fetch_insights.py --days 30

- 再生・いいね・リプライは API の数字で上書きする。プロフアクセス・フォローは API で投稿ごとに取れないので、
  せなさんが記入した値をそのまま残す。
- 自動投稿したもの（analytics/published.csv にあるもの）は型が分かるので posts.csv に入れる。
- 手で投稿したものは型が分からないので analytics/unclassified.csv に入れる。そこで type 列を埋めると、
  次の取り込みで posts.csv に移る。
- その日のフォロワー数を analytics/followers.csv に記録する。
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from threads_api import ThreadsClient, ThreadsError  # noqa: E402
from weekly_report import REQUIRED_FIELDS, TYPE_CATEGORY  # noqa: E402

JST = timezone(timedelta(hours=9))
POSTS_FIELDS = ("date", "time", "slot", "type", "views", "likes", "replies", "profile_visits", "follows", "memo", "post_id")
UNCLASSIFIED_FIELDS = ("post_id", "date", "time", "slot", "type", "views", "likes", "replies", "text")
API_FIELDS = ("views", "likes", "replies")


def read_rows(path: Path) -> tuple[list[str], list[dict]]:
    if not path.exists() or path.stat().st_size == 0:
        return [], []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def write_rows(path: Path, fields, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def slot_for(hour: int) -> str:
    if hour < 11:
        return "朝"
    if hour < 17:
        return "昼"
    return "夜"


def to_jst(timestamp: str) -> datetime:
    return datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S%z").astimezone(JST)


def merge(posts_rows, published, unclassified_rows, fetched):
    """fetched: [{post_id, when(datetime JST), text, views, likes, replies}]

    posts.csv と unclassified.csv の新しい行リストを返す。
    """
    by_id = {r.get("post_id"): r for r in posts_rows if r.get("post_id")}
    unclassified = {r["post_id"]: r for r in unclassified_rows if r.get("post_id")}

    # unclassified.csv で type が埋まった行は posts.csv へ
    for pid, row in list(unclassified.items()):
        if row.get("type", "").strip() in TYPE_CATEGORY and pid not in by_id:
            new = {k: row.get(k, "") for k in POSTS_FIELDS}
            new["type"] = row["type"].strip()
            new["memo"] = (row.get("text") or "").strip().splitlines()[0][:20] if row.get("text") else ""
            posts_rows.append(new)
            by_id[pid] = new
            del unclassified[pid]

    for item in fetched:
        pid = item["post_id"]
        numbers = {k: "" if item.get(k) is None else str(item[k]) for k in API_FIELDS}
        date_s, time_s = f"{item['when']:%Y-%m-%d}", f"{item['when']:%H:%M}"
        if pid in by_id:
            by_id[pid].update(numbers)
            continue
        if pid in published:
            meta = published[pid]
            row = {k: "" for k in POSTS_FIELDS}
            row.update(date=meta["date"], time=meta["time"], slot=meta["slot"], type=meta["type"], memo=meta.get("memo", ""), post_id=pid)
            row.update(numbers)
            posts_rows.append(row)
            by_id[pid] = row
            continue
        slot = slot_for(item["when"].hour)
        # せなさんが手で記入した同じ日・同じ枠の行（post_id なし）が1つだけなら、それと紐づける
        manual = [r for r in posts_rows if not r.get("post_id") and r.get("date") == date_s and r.get("slot") == slot]
        if len(manual) == 1:
            manual[0]["post_id"] = pid
            manual[0].update(numbers)
            by_id[pid] = manual[0]
            continue
        row = unclassified.get(pid, {"post_id": pid, "date": date_s, "time": time_s, "slot": slot, "type": "", "text": item.get("text", "")})
        row.update(numbers)
        unclassified[pid] = row

    posts_rows.sort(key=lambda r: (r.get("date", ""), r.get("time", "")))
    return posts_rows, sorted(unclassified.values(), key=lambda r: (r["date"], r["time"]))


def record_followers(path: Path, day: str, count: int) -> None:
    _, rows = read_rows(path)
    rows = [r for r in rows if r.get("date") != day] + [{"date": day, "followers": str(count)}]
    write_rows(path, ("date", "followers"), sorted(rows, key=lambda r: r["date"]))


def run(args: argparse.Namespace, client=None, now: datetime | None = None) -> int:
    now = now or datetime.now(JST)
    client = client or ThreadsClient.from_env()
    since = int((now - timedelta(days=args.days)).timestamp())
    fetched = []
    for t in client.list_threads(since=since, limit=100):
        if t.get("media_type") == "REPOST_FACADE":
            continue
        try:
            numbers = client.post_insights(t["id"])
        except ThreadsError as e:
            print(f"⚠️ {t['id']} の数字が取れませんでした：{e}")
            numbers = {}
        fetched.append({"post_id": t["id"], "when": to_jst(t["timestamp"]), "text": t.get("text", ""), **numbers})

    fields, posts_rows = read_rows(args.posts)
    missing = [c for c in REQUIRED_FIELDS if fields and c not in fields]
    if missing:
        print(f"エラー: {args.posts} の見出し行に {', '.join(missing)} がありません", file=sys.stderr)
        return 1
    _, published_rows = read_rows(args.published)
    published = {r["post_id"]: r for r in published_rows}
    _, unclassified_rows = read_rows(args.unclassified)

    posts_rows, unclassified = merge(posts_rows, published, unclassified_rows, fetched)
    # せなさんが足した列があれば消さずに残す
    out_fields = list(POSTS_FIELDS) + [f for f in fields if f not in POSTS_FIELDS]
    write_rows(args.posts, out_fields, posts_rows)
    write_rows(args.unclassified, UNCLASSIFIED_FIELDS, unclassified)

    try:
        followers = client.followers_count()
        record_followers(args.followers, f"{now:%Y-%m-%d}", followers)
        print(f"フォロワー：{followers:,}")
    except ThreadsError as e:
        print(f"⚠️ フォロワー数が取れませんでした：{e}")
    print(f"{len(fetched)}件の投稿の数字を取り込みました。型が未分類の投稿：{len(unclassified)}件")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Threads の数字を分析メモに取り込む")
    parser.add_argument("--days", type=int, default=14, help="何日前までの投稿を更新するか（既定14）")
    parser.add_argument("--posts", type=Path, default=Path("analytics/posts.csv"))
    parser.add_argument("--published", type=Path, default=Path("analytics/published.csv"))
    parser.add_argument("--unclassified", type=Path, default=Path("analytics/unclassified.csv"))
    parser.add_argument("--followers", type=Path, default=Path("analytics/followers.csv"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parse_args(argv))
    except ThreadsError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
