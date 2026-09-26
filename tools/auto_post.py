#!/usr/bin/env python3
"""予約キュー（queue/*.txt）から、時刻が来た投稿を Threads に出す。

使い方:
    python3 tools/auto_post.py              # 確認だけ（何も投稿しない）
    python3 tools/auto_post.py --live       # 本当に投稿する

キューのファイルは main ブランチにあるもの＝せなさんが承認済み、として扱う。
投稿できたらファイルを queue/posted/ に移し、analytics/published.csv に投稿IDを記録する。
時刻から --grace 分（既定90分）を過ぎたものは投稿せず「期限切れ」として知らせる。
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from threads_api import MAX_TEXT_LENGTH, ThreadsClient, ThreadsError  # noqa: E402
from weekly_report import SLOTS, TYPE_CATEGORY  # noqa: E402

JST = timezone(timedelta(hours=9))
PUBLISHED_FIELDS = ("post_id", "date", "time", "slot", "type", "memo", "permalink", "file")
LINK_PATTERN = re.compile(r"https?://|note\.com|lin\.ee|amzn\.", re.IGNORECASE)
MAX_POSTS_PER_RUN = 3


class QueueError(ValueError):
    pass


@dataclass
class QueuedPost:
    path: Path
    date: date
    time: str
    slot: str
    type: str
    text: str

    @property
    def scheduled(self) -> datetime:
        hour, minute = map(int, self.time.split(":"))
        return datetime(self.date.year, self.date.month, self.date.day, hour, minute, tzinfo=JST)

    @property
    def memo(self) -> str:
        first = self.text.strip().splitlines()[0]
        return first[:20]


def parse_queue_file(path: Path, allow_links: bool = False) -> QueuedPost:
    raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    head, sep, body = raw.partition("\n---\n")
    if not sep:
        raise QueueError(f"{path.name}: 見出しと本文の間に --- の行がありません")
    meta = {}
    for line in head.splitlines():
        if line.strip():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    for key in ("date", "time", "slot", "type"):
        if not meta.get(key):
            raise QueueError(f"{path.name}: {key} がありません")
    try:
        day = date.fromisoformat(meta["date"])
    except ValueError:
        raise QueueError(f"{path.name}: date は 2026-10-01 の形で書いてください") from None
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", meta["time"]):
        raise QueueError(f"{path.name}: time は 21:30 の形で書いてください")
    if meta["slot"] not in SLOTS:
        raise QueueError(f"{path.name}: slot は 朝/昼/夜 のどれかにしてください")
    if meta["type"] not in TYPE_CATEGORY:
        raise QueueError(f"{path.name}: type は {'/'.join(TYPE_CATEGORY)} のどれかにしてください")
    text = body.strip("\n")
    if not text.strip():
        raise QueueError(f"{path.name}: 本文が空です")
    if len(text) > MAX_TEXT_LENGTH:
        raise QueueError(f"{path.name}: 本文が{len(text)}文字あります（上限{MAX_TEXT_LENGTH}文字）")
    if not allow_links and LINK_PATTERN.search(text):
        raise QueueError(f"{path.name}: リンクが入っています（フォロワー300〜500まではリンクを貼らない方針）")
    return QueuedPost(path, day, meta["time"].zfill(5), meta["slot"], meta["type"], text)


def load_queue(queue_dir: Path, allow_links: bool = False) -> tuple[list[QueuedPost], list[str]]:
    posts, errors = [], []
    for path in sorted(queue_dir.glob("*.txt")):
        try:
            posts.append(parse_queue_file(path, allow_links))
        except QueueError as e:
            errors.append(str(e))
    return sorted(posts, key=lambda p: p.scheduled), errors


def split_due(posts: list[QueuedPost], now: datetime, grace: timedelta):
    due = [p for p in posts if p.scheduled <= now <= p.scheduled + grace]
    expired = [p for p in posts if now > p.scheduled + grace]
    return due, expired


def record_published(csv_path: Path, post: QueuedPost, post_id: str, permalink: str, file_name: str) -> None:
    new = not csv_path.exists() or csv_path.stat().st_size == 0
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PUBLISHED_FIELDS)
        if new:
            writer.writeheader()
        writer.writerow(
            {
                "post_id": post_id,
                "date": post.date.isoformat(),
                "time": post.time,
                "slot": post.slot,
                "type": post.type,
                "memo": post.memo,
                "permalink": permalink,
                "file": file_name,
            }
        )


def run(args: argparse.Namespace, client_factory=ThreadsClient.from_env, now: datetime | None = None) -> int:
    now = now or datetime.now(JST)
    posts, errors = load_queue(args.queue, args.allow_links)
    for message in errors:
        print(f"⚠️ {message}（このファイルは投稿しません）")
    due, expired = split_due(posts, now, timedelta(minutes=args.grace))
    for p in expired:
        print(f"⏰ 期限切れで投稿しませんでした：{p.path.name}（予定 {p.scheduled:%m/%d %H:%M}）")
    if not due:
        print(f"{now:%m/%d %H:%M} 時点で投稿する予約はありません")
        return 1 if errors else 0
    if len(due) > MAX_POSTS_PER_RUN:
        print(f"⚠️ 時刻が来た予約が{len(due)}件あります。念のため先の{MAX_POSTS_PER_RUN}件だけ出します")
        due = due[:MAX_POSTS_PER_RUN]

    client = client_factory() if args.live else None
    recent_texts = set()
    if client:
        # 前回の記録がリポジトリに残らなかった場合に二重投稿しないよう、直近2日の本文と照らす
        since = int((now - timedelta(days=2)).timestamp())
        recent_texts = {(t.get("text") or "").strip() for t in client.list_threads(since=since)}
    posted_dir = args.queue / "posted"
    failed = False
    for p in due:
        if not args.live:
            print(f"[確認のみ] {p.scheduled:%m/%d %H:%M} {p.slot} {p.type}：{p.path.name}\n{p.text}\n")
            continue
        if p.text.strip() in recent_texts:
            print(f"⚠️ 同じ本文がすでに投稿されていたので出しません：{p.path.name}")
            posted_dir.mkdir(parents=True, exist_ok=True)
            p.path.rename(posted_dir / p.path.name)
            continue
        try:
            post_id = client.publish_text(p.text)
        except ThreadsError as e:
            print(f"❌ 投稿に失敗：{p.path.name}：{e}")
            failed = True
            continue
        try:
            permalink = client.permalink(post_id)
        except ThreadsError:
            permalink = ""
        posted_dir.mkdir(parents=True, exist_ok=True)
        target = posted_dir / p.path.name
        p.path.rename(target)
        record_published(args.published, p, post_id, permalink, target.name)
        print(f"✅ 投稿しました：{p.path.name} → {permalink or post_id}")
    return 1 if failed or errors else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="予約キューから時刻が来た投稿を Threads に出す")
    parser.add_argument("--queue", type=Path, default=Path("queue"), help="予約キューのフォルダ")
    parser.add_argument("--published", type=Path, default=Path("analytics/published.csv"), help="投稿済みの記録")
    parser.add_argument("--grace", type=int, default=90, help="予定時刻を過ぎても出してよい分数（既定90）")
    parser.add_argument("--allow-links", action="store_true", help="本文のリンクを許可する（フェーズ2以降）")
    parser.add_argument("--live", action="store_true", help="本当に投稿する（付けなければ確認だけ）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
