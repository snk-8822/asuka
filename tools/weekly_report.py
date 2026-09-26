#!/usr/bin/env python3
"""あすかの分析メモ（CSV）から、日曜の週次振り返りレポートを作る。

使い方:
    python3 tools/weekly_report.py analytics/posts.csv
    python3 tools/weekly_report.py analytics/posts.csv --end 2026-10-04 --followers 180 --phase 1

数字の基準はすべて asuka-roadmap.md（フェーズの切り替え条件・型の配分・KPI）に合わせている。
標準ライブラリだけで動く。
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

NUMERIC_FIELDS = ("views", "likes", "replies", "profile_visits", "follows")
REQUIRED_FIELDS = ("date", "slot", "type") + NUMERIC_FIELDS
SLOTS = ("朝", "昼", "夜")

# 型 → ロードマップの配分カテゴリ
TYPE_CATEGORY = {
    "感情爆発": "感情爆発・あるある",
    "恋愛あるある": "感情爆発・あるある",
    "男磨き": "感情爆発・あるある",
    "二択": "二択・問いかけ",
    "問いかけ": "二択・問いかけ",
    "日常写真": "日常写真",
    "日常": "日常写真",
    "価値観": "価値観・伏線",
    "伏線": "価値観・伏線",
}
CATEGORY_TARGET = {
    "感情爆発・あるある": 0.40,
    "二択・問いかけ": 0.20,
    "日常写真": 0.20,
    "価値観・伏線": 0.20,
}

# ロードマップ「フェーズ1」のKPI
NIGHT_VIEWS_TARGET = 1000
AVG_LIKES_TARGET = 20
CONVERSION_TARGET = 0.03


class DataError(ValueError):
    """CSVの中身がおかしいときに、行番号つきで知らせる。"""


@dataclass
class Post:
    date: date
    time: str
    slot: str
    type: str
    views: int | None
    likes: int | None
    replies: int | None
    profile_visits: int | None
    follows: int | None
    memo: str
    line: int

    @property
    def category(self) -> str:
        return TYPE_CATEGORY[self.type]

    def label(self) -> str:
        head = f"{self.date:%m/%d} {self.slot} {self.type}"
        return f"{head}「{self.memo}」" if self.memo else head


def _parse_int(value: str, field: str, line: int) -> int | None:
    value = value.strip().replace(",", "")
    if value == "":
        return None
    try:
        number = int(value)
    except ValueError:
        raise DataError(f"{line}行目: {field} は数字で書いてください（「{value}」）") from None
    if number < 0:
        raise DataError(f"{line}行目: {field} がマイナスになっています（{number}）")
    return number


def load_posts(path: Path) -> list[Post]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_FIELDS if c not in (reader.fieldnames or [])]
        if missing:
            raise DataError(f"見出し行に {', '.join(missing)} がありません")
        posts = []
        for line, row in enumerate(reader, start=2):
            if not any((v or "").strip() for v in row.values()):
                continue  # 空行は無視
            try:
                day = date.fromisoformat(row["date"].strip())
            except ValueError:
                raise DataError(f"{line}行目: date は 2026-10-01 の形で書いてください（「{row['date']}」）") from None
            slot = row["slot"].strip()
            if slot not in SLOTS:
                raise DataError(f"{line}行目: slot は 朝/昼/夜 のどれかにしてください（「{slot}」）")
            kind = row["type"].strip()
            if kind not in TYPE_CATEGORY:
                allowed = "/".join(TYPE_CATEGORY)
                raise DataError(f"{line}行目: type は {allowed} のどれかにしてください（「{kind}」）")
            numbers = {f: _parse_int(row[f] or "", f, line) for f in NUMERIC_FIELDS}
            posts.append(
                Post(
                    date=day,
                    time=(row.get("time") or "").strip(),
                    slot=slot,
                    type=kind,
                    memo=(row.get("memo") or "").strip(),
                    line=line,
                    **numbers,
                )
            )
    return posts


def week_window(posts: list[Post], end: date | None) -> tuple[date, date, list[Post]]:
    if end is None:
        if not posts:
            raise DataError("投稿の行がまだありません")
        end = max(p.date for p in posts)
    start = end - timedelta(days=6)
    return start, end, [p for p in posts if start <= p.date <= end]


def _average(values: list[int | None]) -> float | None:
    known = [v for v in values if v is not None]
    return sum(known) / len(known) if known else None


def conversion_rate(posts: list[Post]) -> float | None:
    """プロフアクセス→フォローの転換率。両方記入済みの投稿だけで計算する。"""
    pairs = [(p.profile_visits, p.follows) for p in posts if p.profile_visits is not None and p.follows is not None]
    visits = sum(v for v, _ in pairs)
    return sum(f for _, f in pairs) / visits if visits else None


def rank_posts(posts: list[Post]) -> list[Post]:
    """いいね順（同数なら再生順）。いいね未記入は順位づけから外す。"""
    rated = [p for p in posts if p.likes is not None]
    return sorted(rated, key=lambda p: (p.likes, p.views or 0), reverse=True)


def top_and_bottom(posts: list[Post], n: int = 3) -> tuple[list[Post], list[Post]]:
    ranked = rank_posts(posts)
    top = ranked[:n]
    # 上位と下位が重ならないよう、少ない週は残りから下位を取る
    rest = ranked[len(top):]
    bottom = list(reversed(rest[-n:])) if rest else []
    return top, bottom


def next_week_adjustments(top: list[Post], bottom: list[Post]) -> dict[str, int]:
    """上位3本に入った型は+1本、下位3本に入った型は-1本（ロードマップ7章の毎週ルール）。

    同じ型が何本入っても増減は1本ずつ。上位にも下位にも入った型は据え置き。
    """
    change = {kind: 1 for kind in {p.type for p in top}}
    for kind in {p.type for p in bottom}:
        change[kind] = change.get(kind, 0) - 1
    return {k: v for k, v in change.items() if v}


@dataclass
class Check:
    name: str
    target: str
    actual: str
    ok: bool | None  # None = 未入力で判定できない


def _fmt(value: float | int | None, digits: int = 1) -> str:
    if value is None:
        return "未入力"
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.{digits}f}"


def _pct(value: float | None) -> str:
    return "未入力" if value is None else f"{value * 100:.1f}%"


def _at_least(value, target) -> bool | None:
    return None if value is None else value >= target


def phase_checks(phase: int, week: list[Post], args: argparse.Namespace) -> tuple[str, list[Check], str]:
    """(見出し, 条件のリスト, 合格のしかた) を返す。合格のしかたは "all" か "any"。"""
    avg_likes = _average([p.likes for p in week])
    if phase == 1:
        conv = conversion_rate(week)
        return (
            "フェーズ1 → 2（note開設）",
            [
                Check("フォロワー", "300以上", _fmt(args.followers), _at_least(args.followers, 300)),
                Check("直近7日の平均いいね", f"{AVG_LIKES_TARGET}以上", _fmt(avg_likes), _at_least(avg_likes, AVG_LIKES_TARGET)),
                Check("プロフ→フォロー転換", "3%以上", _pct(conv), _at_least(conv, CONVERSION_TARGET)),
            ],
            "all",
        )
    if phase == 2:
        return (
            "フェーズ2 → 3（有料note＋男磨き）",
            [
                Check("無料note2本の合計閲覧", "500以上", _fmt(args.note_free_views), _at_least(args.note_free_views, 500)),
                Check("無料note2本の合計スキ", "50以上", _fmt(args.note_suki), _at_least(args.note_suki, 50)),
            ],
            "any",
        )
    if phase == 3:
        grooming = _average([p.likes for p in week if p.type == "男磨き"])
        grooming_ok = None if grooming is None or avg_likes is None else grooming >= avg_likes
        return (
            "フェーズ3 → 4（アフィリエイト本格化）",
            [
                Check("フォロワー", "1,000以上", _fmt(args.followers), _at_least(args.followers, 1000)),
                Check("男磨き系の平均いいね", f"全体平均（{_fmt(avg_likes)}）以上", _fmt(grooming), grooming_ok),
                Check("note月間閲覧", "1,000以上", _fmt(args.note_monthly_views), _at_least(args.note_monthly_views, 1000)),
            ],
            "all",
        )
    if phase == 4:
        return (
            "フェーズ4 → LINE・メンバーシップ検討",
            [Check("月の購入者（有料＋アフィ）", "30人以上", _fmt(args.buyers), _at_least(args.buyers, 30))],
            "all",
        )
    raise DataError("--phase は 1〜4 で指定してください")


def phase_verdict(checks: list[Check], mode: str) -> str:
    results = [c.ok for c in checks]
    passed = all(r is True for r in results) if mode == "all" else any(r is True for r in results)
    if passed:
        return "条件クリア。次のフェーズに進めます"
    if None in results:
        return "未入力の数字があるので、まだ判定できません"
    return "まだ条件を満たしていません。今のフェーズを続けます"


def _mark(ok: bool | None) -> str:
    return {True: "✅", False: "❌", None: "―"}[ok]


def build_report(posts: list[Post], args: argparse.Namespace) -> str:
    start, end, week = week_window(posts, args.end)
    out = [f"# 週次振り返り {start:%Y-%m-%d}〜{end:%Y-%m-%d}", ""]
    if not week:
        out.append("この7日間の投稿が記入されていません。")
        return "\n".join(out) + "\n"

    night = [p for p in week if p.slot == "夜"]
    night_days = {p.date for p in night}
    missing_nights = [start + timedelta(days=i) for i in range(7) if start + timedelta(days=i) not in night_days]
    night_views = _average([p.views for p in night])
    avg_likes = _average([p.likes for p in week])
    conv = conversion_rate(week)

    out += [
        "## 数字（フェーズ1のKPIと比較）",
        "",
        "| 指標 | 今週 | 目標 |",
        "|---|---|---|",
        f"| 投稿数 | {len(week)}本 | 週15〜20本 |",
        f"| 夜の主力の平均再生 | {_fmt(night_views, 0)} | {NIGHT_VIEWS_TARGET:,}以上 |",
        f"| 投稿あたり平均いいね | {_fmt(avg_likes)} | {AVG_LIKES_TARGET}以上 |",
        f"| プロフ→フォロー転換 | {_pct(conv)} | 3〜5% |",
        "",
    ]
    if missing_nights:
        days = "、".join(f"{d:%m/%d}" for d in missing_nights)
        out += [f"⚠️ 夜の主力が記入されていない日：{days}（夜の1本は毎日必須）", ""]

    out += ["## 型の配分", "", "| 型 | 本数 | 割合 | 目安 | 平均いいね |", "|---|---|---|---|---|"]
    for category, target in CATEGORY_TARGET.items():
        group = [p for p in week if p.category == category]
        share = len(group) / len(week)
        out.append(
            f"| {category} | {len(group)}本 | {share * 100:.0f}% | {target * 100:.0f}% | {_fmt(_average([p.likes for p in group]))} |"
        )
    out.append("")

    top, bottom = top_and_bottom(week)
    out += ["## 伸びた上位3本（いいね順）", ""]
    out += [f"{i}. {p.label()} ― いいね{_fmt(p.likes)}／再生{_fmt(p.views)}" for i, p in enumerate(top, 1)] or ["（いいねが記入された投稿がありません）"]
    out += ["", "共通点（1行で）：", "", "## 伸びなかった下位3本", ""]
    out += [f"{i}. {p.label()} ― いいね{_fmt(p.likes)}／再生{_fmt(p.views)}" for i, p in enumerate(bottom, 1)] or ["（比べられる投稿が足りません）"]
    out.append("")

    changes = next_week_adjustments(top, bottom)
    out += ["## 来週の型の調整", ""]
    if changes:
        out += [f"- {kind}：{'+' if n > 0 else ''}{n}本" for kind, n in sorted(changes.items(), key=lambda kv: -kv[1])]
    else:
        out.append("- 上位と下位の型が打ち消し合ったので、今週と同じ配分で")
    out += ["", "来週の夜の主力7本のネタ：", ""]

    heading, checks, mode = phase_checks(args.phase, week, args)
    out += [f"## フェーズ判定：{heading}", "", "| 条件 | 今 | 基準 | |", "|---|---|---|---|"]
    out += [f"| {c.name} | {c.actual} | {c.target} | {_mark(c.ok)} |" for c in checks]
    if mode == "any":
        out += ["", "（どれか1つ満たせばOK）"]
    out += ["", f"→ {phase_verdict(checks, mode)}", ""]
    return "\n".join(out)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="分析メモCSVから週次振り返りを作る")
    parser.add_argument("csv", type=Path, help="分析メモのCSV（analytics/posts.csv）")
    parser.add_argument("--end", type=date.fromisoformat, help="集計する週の最終日 YYYY-MM-DD（省略時はCSVの最新日）")
    parser.add_argument("--phase", type=int, default=1, choices=range(1, 5), help="今のフェーズ（1〜4、既定1）")
    parser.add_argument("--followers", type=int, help="今のフォロワー数（省略時は analytics/followers.csv の最新値）")
    parser.add_argument("--followers-csv", type=Path, default=Path("analytics/followers.csv"), help=argparse.SUPPRESS)
    parser.add_argument("--note-free-views", type=int, help="無料note2本の合計閲覧（フェーズ2）")
    parser.add_argument("--note-suki", type=int, help="無料note2本の合計スキ（フェーズ2）")
    parser.add_argument("--note-monthly-views", type=int, help="noteの月間閲覧（フェーズ3）")
    parser.add_argument("--buyers", type=int, help="月の購入者数・有料＋アフィ（フェーズ4）")
    parser.add_argument("-o", "--output", type=Path, help="レポートの保存先（省略時は画面に表示）")
    return parser.parse_args(argv)


def latest_followers(path: Path) -> int | None:
    """fetch_insights.py が毎日記録しているフォロワー数の最新値。"""
    if not path.exists():
        return None
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("followers") or "").strip()]
    return int(rows[-1]["followers"]) if rows else None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.followers is None:
        args.followers = latest_followers(args.followers_csv)
    try:
        report = build_report(load_posts(args.csv), args)
    except (DataError, FileNotFoundError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"{args.output} に保存しました")
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
