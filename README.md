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

## 自動投稿・数字の取り込み・案の修正（ループ）

```
毎朝 6:52   Claude が当日の昼・夜の案を2〜3パターン出す（直近の数字と週次振り返りを見て型を調整）
              ↓ せなさんが選ぶ → Claude が queue/ に入れる PR を作る → せなさんがマージ＝投稿OK
投稿枠       GitHub Actions（auto-post）が時刻の来た予約を Threads に投稿
毎日 4:30   GitHub Actions（fetch-insights）が直近14日の再生・いいね・リプライとフォロワー数を取り込む
月曜 4:30   先週の振り返りを reports/ に保存
日曜 19:48  Claude が振り返りを読み、翌週の夜の主力7本を「伸びた型+1・伸びなかった型-1」で組み直す
```

### はじめに一度だけ（せなさんの作業）

1. [Meta for Developers](https://developers.facebook.com/) でアプリを作り、ユースケースに「Threads API にアクセス」を選ぶ
2. 権限に `threads_basic` `threads_content_publish` `threads_manage_insights` を追加
3. アプリの「役割」で @asuka.life1211 を Threads テスターに追加し、Threads アプリの「設定 → アカウント → ウェブサイトのアクセス許可」で招待を承認
4. アクセストークンを発行して長期トークン（60日有効）に交換し、あわせてユーザーIDを確認する（`GET https://graph.threads.net/v1.0/me?fields=id,username`）
5. GitHub のこのリポジトリの Settings → Secrets and variables → Actions で
   - Secrets に `THREADS_ACCESS_TOKEN`（長期トークン）と `THREADS_USER_ID`
   - 本番投稿を始めてよいときだけ、Variables に `AUTO_POST_ENABLED` = `true`

`AUTO_POST_ENABLED` が無い間は、auto-post は「この予約を出す予定」と表示するだけで何も投稿しません。
止めたいときはこの変数を消すか `false` にすれば、次の回から投稿しなくなります。

トークンは60日で切れます。切れると fetch-insights が失敗して GitHub からメールが来るので、そのときは4.をやり直して Secrets を入れ替えてください。

### 取り込みのルール

- 再生・いいね・リプライは API の値で上書きします。**プロフアクセス・フォローは投稿ごとには API で取れない**ので、これまでどおり手で記入した値を残します（週次のフォロー転換率はこの記入分で計算）
- 自動投稿したものは型が分かるので、そのまま `analytics/posts.csv` に入ります
- 手で投稿したものは `analytics/unclassified.csv` に入ります。`type` 列を埋める（Claude に頼んでもOK）と、次の取り込みで posts.csv に移ります。同じ日・同じ枠に手で書いた行が1つだけあれば、そこに数字を足します
- フォロワー数は毎日 `analytics/followers.csv` に記録され、週次レポートの `--followers` を省略したときに使われます
