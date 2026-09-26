import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import weekly_report as wr  # noqa: E402

HEADER = "date,time,slot,type,views,likes,replies,profile_visits,follows,memo\n"


def write_csv(tmp: Path, body: str) -> Path:
    path = tmp / "posts.csv"
    path.write_text(HEADER + body, encoding="utf-8")
    return path


def args(*extra: str):
    return wr.parse_args(["dummy.csv", *extra])


class LoadPostsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_reads_rows_and_blank_numbers(self):
        path = write_csv(self.tmp, "2026-10-01,21:00,夜,感情爆発,\"1,200\",30,,40,2,メモ\n\n")
        [post] = wr.load_posts(path)
        self.assertEqual(post.date, date(2026, 10, 1))
        self.assertEqual(post.views, 1200)
        self.assertIsNone(post.replies)
        self.assertEqual(post.category, "感情爆発・あるある")

    def test_accepts_excel_bom(self):
        path = self.tmp / "posts.csv"
        path.write_text("﻿" + HEADER + "2026-10-01,,昼,二択,1,1,1,1,1,\n", encoding="utf-8")
        self.assertEqual(len(wr.load_posts(path)), 1)

    def test_errors_name_the_line(self):
        cases = {
            "2026/10/01,,夜,感情爆発,1,1,1,1,1,\n": "2行目: date",
            "2026-10-01,,深夜,感情爆発,1,1,1,1,1,\n": "2行目: slot",
            "2026-10-01,,夜,自撮り,1,1,1,1,1,\n": "2行目: type",
            "2026-10-01,,夜,感情爆発,たくさん,1,1,1,1,\n": "2行目: views",
            "2026-10-01,,夜,感情爆発,1,-3,1,1,1,\n": "2行目: likes",
        }
        for body, message in cases.items():
            with self.subTest(body=body):
                with self.assertRaisesRegex(wr.DataError, message):
                    wr.load_posts(write_csv(self.tmp, body))

    def test_missing_header_column(self):
        path = self.tmp / "posts.csv"
        path.write_text("date,slot,type\n", encoding="utf-8")
        with self.assertRaisesRegex(wr.DataError, "views"):
            wr.load_posts(path)


def post(day: int, slot="夜", kind="感情爆発", views=1000, likes=20, visits=40, follows=2, memo=""):
    return wr.Post(date(2026, 10, day), "", slot, kind, views, likes, 0, visits, follows, memo, 0)


class MetricsTest(unittest.TestCase):
    def test_week_window_defaults_to_latest_date(self):
        posts = [post(1), post(8), post(2)]
        start, end, week = wr.week_window(posts, None)
        self.assertEqual((start, end), (date(2026, 10, 2), date(2026, 10, 8)))
        self.assertEqual([p.date.day for p in week], [8, 2])

    def test_conversion_ignores_unfilled_posts(self):
        posts = [post(1, visits=50, follows=2), post(2, visits=None, follows=5), post(3, visits=0, follows=0)]
        self.assertAlmostEqual(wr.conversion_rate(posts), 0.04)
        self.assertIsNone(wr.conversion_rate([post(1, visits=None)]))

    def test_top_and_bottom_do_not_overlap(self):
        posts = [post(d, likes=l) for d, l in [(1, 5), (2, 40), (3, 10), (4, 30)]]
        top, bottom = wr.top_and_bottom(posts)
        self.assertEqual([p.likes for p in top], [40, 30, 10])
        self.assertEqual([p.likes for p in bottom], [5])

    def test_ties_break_on_views_and_unfilled_likes_are_skipped(self):
        posts = [post(1, likes=20, views=500), post(2, likes=20, views=900), post(3, likes=None)]
        self.assertEqual([p.date.day for p in wr.rank_posts(posts)], [2, 1])

    def test_adjustments_move_each_type_by_one(self):
        top = [post(1, kind="感情爆発"), post(2, kind="感情爆発"), post(3, kind="二択")]
        bottom = [post(4, kind="日常写真"), post(5, kind="日常写真"), post(6, kind="二択")]
        self.assertEqual(wr.next_week_adjustments(top, bottom), {"感情爆発": 1, "日常写真": -1})


class PhaseTest(unittest.TestCase):
    def verdict(self, phase, week, *extra):
        _, checks, mode = wr.phase_checks(phase, week, args("--phase", str(phase), *extra))
        return wr.phase_verdict(checks, mode)

    def test_phase1_needs_all_three(self):
        good = [post(1, likes=25, visits=100, follows=4)]
        self.assertIn("条件クリア", self.verdict(1, good, "--followers", "300"))
        self.assertIn("まだ条件", self.verdict(1, good, "--followers", "299"))
        self.assertIn("判定できません", self.verdict(1, good))
        low_conv = [post(1, likes=25, visits=100, follows=2)]
        self.assertIn("まだ条件", self.verdict(1, low_conv, "--followers", "500"))

    def test_phase2_needs_either(self):
        self.assertIn("条件クリア", self.verdict(2, [], "--note-suki", "50"))
        self.assertIn("条件クリア", self.verdict(2, [], "--note-free-views", "500", "--note-suki", "3"))
        self.assertIn("まだ条件", self.verdict(2, [], "--note-free-views", "499", "--note-suki", "49"))

    def test_phase3_compares_grooming_posts_to_average(self):
        week = [post(1, kind="男磨き", likes=30), post(2, likes=10)]
        extra = ("--followers", "1000", "--note-monthly-views", "1000")
        self.assertIn("条件クリア", self.verdict(3, week, *extra))
        weak = [post(1, kind="男磨き", likes=10), post(2, likes=30)]
        self.assertIn("まだ条件", self.verdict(3, weak, *extra))
        self.assertIn("判定できません", self.verdict(3, [post(1)], *extra))

    def test_phase4_buyers(self):
        self.assertIn("条件クリア", self.verdict(4, [], "--buyers", "30"))


class ReportTest(unittest.TestCase):
    def test_example_csv_end_to_end(self):
        example = ROOT / "analytics" / "posts.example.csv"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.md"
            code = wr.main([str(example), "--followers", "180", "-o", str(out)])
            self.assertEqual(code, 0)
            report = out.read_text(encoding="utf-8")
        self.assertIn("# 週次振り返り 2026-09-28〜2026-10-04", report)
        self.assertIn("| 夜の主力の平均再生 | 1,113 | 1,000以上 |", report)
        self.assertIn("| フォロワー | 180 | 300以上 | ❌ |", report)
        self.assertIn("- 感情爆発：+1本", report)

    def test_flags_missing_night_posts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(Path(tmp), "2026-10-01,,夜,感情爆発,1,1,1,1,1,\n2026-10-03,,昼,二択,1,1,1,1,1,\n")
            report = wr.build_report(wr.load_posts(path), args("--end", "2026-10-03"))
        self.assertIn("夜の主力が記入されていない日：09/27、09/28、09/29、09/30、10/02、10/03", report)

    def test_empty_week(self):
        report = wr.build_report([post(1)], args("--end", "2026-10-20"))
        self.assertIn("この7日間の投稿が記入されていません", report)

    def test_empty_csv_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(Path(tmp), "")
            self.assertEqual(wr.main([str(path)]), 1)


if __name__ == "__main__":
    unittest.main()
