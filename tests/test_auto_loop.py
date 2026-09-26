import csv
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import auto_post as ap  # noqa: E402
import fetch_insights as fi  # noqa: E402
import threads_api  # noqa: E402

JST = ap.JST


def queue_file(folder: Path, name: str, text: str = "あのさーー!?\n目標：寝る", **meta) -> Path:
    head = {"date": "2026-10-01", "time": "21:30", "slot": "夜", "type": "感情爆発", **meta}
    path = folder / name
    path.write_text("\n".join(f"{k}: {v}" for k, v in head.items()) + "\n---\n" + text + "\n", encoding="utf-8")
    return path


class FakeClient:
    def __init__(self, threads=None, insights=None, followers=120):
        self.published = []
        self.threads = threads or []
        self.insights = insights or {}
        self.followers = followers

    def publish_text(self, text):
        self.published.append(text)
        return f"id{len(self.published)}"

    def permalink(self, media_id):
        return f"https://www.threads.net/@asuka.life1211/post/{media_id}"

    def list_threads(self, since=None, limit=50):
        return self.threads

    def post_insights(self, media_id):
        return self.insights.get(media_id, {})

    def followers_count(self):
        return self.followers


def read(path: Path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class AutoPostTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.queue = self.tmp / "queue"
        self.queue.mkdir()
        self.published = self.tmp / "published.csv"

    def tearDown(self):
        self._tmp.cleanup()

    def args(self, *extra):
        return ap.parse_args(["--queue", str(self.queue), "--published", str(self.published), *extra])

    def test_parse_rejects_links_and_bad_type(self):
        queue_file(self.queue, "a.txt", text="noteはこちら https://note.com/x")
        queue_file(self.queue, "b.txt", type="自撮り")
        posts, errors = ap.load_queue(self.queue)
        self.assertEqual(posts, [])
        self.assertIn("リンク", errors[0])
        self.assertIn("type", errors[1])
        posts, _ = ap.load_queue(self.queue, allow_links=True)
        self.assertEqual(len(posts), 1)

    def test_dry_run_posts_nothing(self):
        queue_file(self.queue, "a.txt")
        client = FakeClient()
        ap.run(self.args(), client_factory=lambda: client, now=datetime(2026, 10, 1, 21, 40, tzinfo=JST))
        self.assertEqual(client.published, [])
        self.assertTrue((self.queue / "a.txt").exists())

    def test_live_posts_only_due_and_records(self):
        queue_file(self.queue, "due.txt")
        queue_file(self.queue, "later.txt", time="22:30", text="まだ")
        queue_file(self.queue, "old.txt", date="2026-09-30", text="期限切れ")
        client = FakeClient()
        code = ap.run(self.args("--live"), client_factory=lambda: client, now=datetime(2026, 10, 1, 21, 40, tzinfo=JST))
        self.assertEqual(code, 0)
        self.assertEqual(client.published, ["あのさーー!?\n目標：寝る"])
        self.assertTrue((self.queue / "posted" / "due.txt").exists())
        self.assertTrue((self.queue / "later.txt").exists())
        self.assertTrue((self.queue / "old.txt").exists())
        [row] = read(self.published)
        self.assertEqual((row["post_id"], row["type"], row["memo"]), ("id1", "感情爆発", "あのさーー!?"))

    def test_skips_text_already_on_threads(self):
        queue_file(self.queue, "due.txt")
        client = FakeClient(threads=[{"text": "あのさーー!?\n目標：寝る"}])
        ap.run(self.args("--live"), client_factory=lambda: client, now=datetime(2026, 10, 1, 21, 40, tzinfo=JST))
        self.assertEqual(client.published, [])
        self.assertTrue((self.queue / "posted" / "due.txt").exists())


class PublishTextTest(unittest.TestCase):
    def test_two_step_publish(self):
        calls = []

        def transport(method, url, params):
            calls.append((method, url.rsplit("/", 1)[-1], params))
            return {"id": "c1"} if url.endswith("/threads") else {"id": "p1"}

        client = threads_api.ThreadsClient("tok", "u1", transport=transport, sleep=lambda s: None)
        self.assertEqual(client.publish_text("こんにちは"), "p1")
        self.assertEqual([c[1] for c in calls], ["threads", "threads_publish"])
        self.assertEqual(calls[1][2]["creation_id"], "c1")

    def test_too_long(self):
        client = threads_api.ThreadsClient("tok", "u1", transport=lambda *a: {}, sleep=lambda s: None)
        with self.assertRaises(threads_api.ThreadsError):
            client.publish_text("あ" * 501)


class FetchInsightsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.posts = self.tmp / "posts.csv"
        self.posts.write_text(
            "date,time,slot,type,views,likes,replies,profile_visits,follows,memo\n"
            "2026-10-01,12:15,昼,二択,,,,18,1,手で記入\n",
            encoding="utf-8",
        )
        (self.tmp / "published.csv").write_text(
            "post_id,date,time,slot,type,memo,permalink,file\nP1,2026-10-01,21:30,夜,感情爆発,既読放置,,a.txt\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def run_fetch(self, client):
        args = fi.parse_args([
            "--posts", str(self.posts),
            "--published", str(self.tmp / "published.csv"),
            "--unclassified", str(self.tmp / "unclassified.csv"),
            "--followers", str(self.tmp / "followers.csv"),
        ])
        return fi.run(args, client=client, now=datetime(2026, 10, 2, 4, 30, tzinfo=JST))

    def test_merge_published_manual_and_unknown(self):
        client = FakeClient(
            threads=[
                {"id": "P1", "timestamp": "2026-10-01T12:30:00+0000", "text": "既読放置"},
                {"id": "P2", "timestamp": "2026-10-01T03:16:00+0000", "text": "可愛い？面倒？"},
                {"id": "P3", "timestamp": "2026-09-30T23:05:00+0000", "text": "おはよ☁️"},
            ],
            insights={"P1": {"views": 1350, "likes": 31, "replies": 14}, "P2": {"views": 420, "likes": 12, "replies": 9}, "P3": {"views": 200, "likes": 5, "replies": 0}},
        )
        self.assertEqual(self.run_fetch(client), 0)
        rows = {r["post_id"]: r for r in read(self.posts)}
        self.assertEqual(rows["P1"]["type"], "感情爆発")
        self.assertEqual(rows["P1"]["likes"], "31")
        # 手で書いた昼の行に紐づき、プロフアクセスは残る
        self.assertEqual((rows["P2"]["memo"], rows["P2"]["profile_visits"], rows["P2"]["views"]), ("手で記入", "18", "420"))
        [unknown] = read(self.tmp / "unclassified.csv")
        self.assertEqual((unknown["post_id"], unknown["slot"], unknown["time"]), ("P3", "朝", "08:05"))
        self.assertEqual(read(self.tmp / "followers.csv"), [{"date": "2026-10-02", "followers": "120"}])

        # 未分類に type を書くと、次の取り込みで posts.csv に移る
        path = self.tmp / "unclassified.csv"
        path.write_text(path.read_text(encoding="utf-8").replace(",朝,,", ",朝,日常写真,"), encoding="utf-8")
        self.run_fetch(client)
        self.assertIn("P3", {r["post_id"] for r in read(self.posts)})
        self.assertEqual(read(path), [])
        # 週次レポートがそのまま読める
        import weekly_report as wr
        self.assertEqual(len(wr.load_posts(self.posts)), 3)


if __name__ == "__main__":
    unittest.main()
