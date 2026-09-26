# asuka
asuka money

Threads @asuka.life1211 の運用メモと道具を置くリポジトリ。

## 分析メモと週次振り返り

ロードマップ（フェーズ0「分析メモを作る」と、毎週日曜の振り返り）用の道具です。

### 1. 数字を記入する

`analytics/posts.csv` に1投稿1行で書き足します（Excel・Numbers・Googleスプレッドシートで開いてOK、CSVで保存）。
書き方の見本は `analytics/posts.example.csv` にあります。

| 列 | 書くこと |
|---|---|
| date | 投稿日 `2026-10-01` |
| time | 投稿時刻 `21:30`（空欄可） |
| slot | `朝` / `昼` / `夜` |
| type | `感情爆発` `恋愛あるある` `男磨き` `二択` `問いかけ` `日常写真` `価値観` `伏線` のどれか |
| views / likes / replies | 再生・いいね・リプライ |
| profile_visits / follows | その投稿からのプロフアクセス・フォロー |
| memo | 冒頭の一言など（空欄可） |

数字がまだ分からない欄は空欄のままで大丈夫です（その欄は平均から外れます）。

### 2. 日曜にレポートを出す

```
python3 tools/weekly_report.py analytics/posts.csv --followers 180
```

直近7日（CSVの最新日まで）について、次をまとめて表示します。

- フェーズ1のKPI（夜の主力の平均再生・平均いいね・プロフ→フォロー転換）との比較
- 夜の主力が抜けている日
- 型の配分（目安 40/20/20/20%）と型ごとの平均いいね
- 伸びた上位3本・下位3本と、来週の型の増減（上位の型+1本、下位の型-1本）
- 次のフェーズへ進む条件を満たしているか

よく使うオプション：

- `--end 2026-10-04` 集計する週の最終日を指定
- `--phase 2` 今のフェーズ（1〜4）。フェーズ2以降は `--note-free-views` `--note-suki` `--note-monthly-views` `--buyers` で note の数字を渡す
- `-o reports/2026-10-04.md` ファイルに保存

Python 3.10以上の標準ライブラリだけで動きます。テストは `python3 -m unittest discover -s tests`。
