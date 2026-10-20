# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクト概要

「にゅーす・あつめーる」。FEEDYライクな技術情報アグリゲーターツール。複数ソースから最新の技術情報を収集・集約し、モダンなUIで表示するローカル専用ツール。

**コアコンセプト**: 「最新情報の検知」が主目的。記事管理やじっくり読む用途ではない。気になった記事は外部ブラウザで開いて読む。

## 技術スタック

- **言語**: Python（フロントエンド・バックエンドともにPythonのみ）
- **UIフレームワーク**: Flet 0.85（Flutterベースのデスクトップアプリ、Webサーバ不要）
- **DB**: SQLite（SQLAlchemy経由）
- **スケジューラ**: APScheduler BackgroundScheduler（スレッド型、定期自動取得）
- **フィード取得**: feedparser（RSS）、httpx（REST API）、BeautifulSoup4（スクレイピング）
- **パッケージ管理**: uv

## 情報ソース

| ソース | 取得方法 |
|---|---|
| Zenn | RSS (`https://zenn.dev/feed`) |
| Qiita | RSS (`https://qiita.com/popular-items/feed`) |
| HackerNews | 公式API (`https://hacker-news.firebaseio.com/v0/`) |
| GitHub Trending | スクレイピング |
| dev.classmethod.jp | RSS (`https://dev.classmethod.jp/feed/`) |
| 任意RSS | feedparser（ユーザー追加URL） |

ソースは `FeedSource` テーブルで管理。設定画面から追加・削除・ON/OFF切り替え可能。

## 機能一覧

- 記事一覧表示（カード形式 / リスト形式 切り替え）
- ページネーション（1ページ100件、SQL LIMIT/OFFSET）
- ソースフィルタリング・キーワード検索
- 公開日ソート（降順 / 昇順）
- 既読管理（既読記事の視覚的区別）
  - 個別既読ボタン（URLを開かずに既読）
  - チェックボックスによる複数選択 → 一括既読（確認不要）
- 記事の任意削除（削除前確認ダイアログあり）
- ブックマーク
- 定期自動取得（全ソース共通間隔、UI上で変更可能）
- 手動リフレッシュ
- 設定画面：ソース管理・カテゴリ管理・取得間隔・保持件数
  - RSS追加時にカテゴリも同時選択可能
- カテゴリ管理（追加・リネーム・削除・↑↓並び替え）
- ソース管理（カテゴリ割り当て・↑↓並び替え・ON/OFF・RSS追加）

## UI仕様

### 記事カード／リストアイテムの表示項目
- チェックボックス（複数選択用）
- タイトル（クリックで外部ブラウザ起動 + 既読）
- 記事の概要（OGP `og:description` から取得、フィードに含まれる場合はそちらを優先）
- 記事の公開日時・取得日時（JST表示）
- ソースバッジ（Zenn / Qiita / HackerNews 等）
- NEWバッジ（取得から24時間以内）
- 既読ボタン（未読記事のみ表示、URLを開かず既読にする）
- ブックマークボタン
- 削除ボタン

※サムネイルは表示しない

### サイドバー
- ソースフィルタ Dropdown（クリックで絞り込み、カテゴリ区切り行付き）
- 既読フィルタ ラジオボタン（すべて / 未読のみ / 既読のみ）
- ソースごとの未読件数カウンター（カテゴリ別、クリックでソース/カテゴリ絞り込み）
- 最終取得日時の表示（JST）
- 手動リフレッシュボタン
- ブックマーク・設定への遷移ボタン
- 無効化ソースの記事はメイン一覧に表示しない

### ツールバー
- キーワード検索
- 一括既読ボタン（選択中の記事がある場合のみ表示）
- 公開日ソート Dropdown（降順 / 昇順）
- カード / リスト表示切り替えボタン

### ページナビゲーション（記事エリア下部）
- 「◀ 1–50 / 1234件 ▶」形式で表示
- 50件以下のときは非表示

## データ保持ルール

### 記事
- 保持方式: 件数ベース（**ソースごと**の上限）
- デフォルト上限: 100件/ソース、最大 9999件（UI上で変更可能）
- 上限超過時: 古い記事から自動削除（ブックマーク済みを除く）
- 任意削除: UIから個別に削除可能（削除前確認ダイアログあり、復元機能なし）

### ブックマーク
- 記事とは別管理（記事の保持件数上限を超えても削除されない）
- デフォルト上限: 100件（トータル）、最大 9999件（UI上で変更可能）
- 上限超過時: 追加操作をブロックし、エラーダイアログを表示して不要なブックマーク削除を促す
- 任意削除: UIから個別に削除可能（削除前確認ダイアログあり、復元機能なし）

## 起動コマンド

```bash
.venv/Scripts/python.exe -m get_new_tech_info.get_new_tech_info
```

## exe化

`flet pack` + PyInstaller で単一exeをビルド。`uv` が PATH にない場合は `.venv/Scripts/flet.exe` を直接使う。

```bash
.venv/Scripts/flet.exe pack main.py --name get_new_tech_info --icon assets/icon.ico -y
```

出力先: `dist/get_new_tech_info.exe`

DB保存先: `%LOCALAPPDATA%\get_new_tech_info\get_new_tech_info.db`（exe/開発実行で共通）。旧保存先（exe横 / プロジェクト直下）のDBは、新保存先が無い初回起動時に複製される。

## アーキテクチャ

```
get_new_tech_info/
├── get_new_tech_info/
│   ├── ui/
│   │   ├── main_view.py       # 記事一覧画面
│   │   ├── bookmarks_view.py  # ブックマーク画面
│   │   ├── settings_view.py   # 設定画面
│   │   └── article_card.py    # カード/リスト行コンポーネント
│   ├── fetchers/              # ソースごとの取得ロジック（1ソース1ファイル）
│   ├── models.py              # SQLAlchemyモデル（Article, FeedSource, AppSettings, SourceCategory）
│   ├── database.py            # DB初期化・セッション管理・設定読み書き・ソースCRUD
│   ├── scheduler.py           # APScheduler BackgroundScheduler
│   └── get_new_tech_info.py   # エントリポイント（ft.run(main)）
├── main.py                    # flet pack 用エントリポイント
├── assets/
│   ├── favicon.ico
│   ├── icon.png
│   └── icon.ico         # flet pack用（PNGから生成）
└── pyproject.toml
```

### ルーティング
content-swap方式（`ft.View`スタック不使用）。`page.on_route_change` で `ft.Container` の `.content` を差し替え。
画面遷移は `async def` + `await page.push_route("/path")`。

### データフロー

1. APScheduler（または手動トリガー）→ `fetchers/` 各クラス → SQLite保存
2. `rebuild_articles()` → SQLiteから記事取得 → `page.update()` でUI更新
3. 既読・ブックマーク・選択操作 → SQLite即時反映 → `rebuild_articles()` 再呼び出し

### 設計方針

- `fetchers/` は `BaseFetcher` を継承。`_build_fetchers()` がDB上の有効ソースを読んでフェッチャーを生成
- DBアクセスは `database.py` に集約、UIロジックと分離
- 取得間隔・有効ソースはDB（`AppSettings`, `FeedSource`）で永続化、再起動後も保持
- 記事URLにユニーク制約を設け、重複記事は1件として管理
- OGP概要取得はバックグラウンドスレッドで非同期実行、記事取得をブロックしない
- `register_on_fetch_done` は毎回 `clear()` してから登録（画面再構築時のコールバック重複防止）
- `threading.Lock` で `rebuild_articles()` を排他制御（バックグラウンド取得とUIスレッドの競合防止）
- 複数選択状態は `state["selected_ids"]` (set) で管理、画面遷移やフィルタ変更時もクリアしない
- `_article_refs: dict[int, dict]` でウィジェット参照（container/read_btn/checkbox）を保持し、一括既読時にin-place更新で即時視覚フィードバック
- 一括既読: 即時視覚更新（opacity/checkbox）→ バックグラウンドスレッドでDB一括UPDATE → rebuild_articles()
- 検索入力は300msデバウンス（`threading.Timer`）
- カテゴリは `SourceCategory` テーブルで管理、`FeedSource` に `category_id` / `display_order` を持つ
- サイドバーの未読カウンターはカテゴリ行クリックでカテゴリ絞り込み、ソース行クリックでソース絞り込み
- カテゴリ左端の矢印アイコンクリックでソース行を折りたたみ/展開（状態は `_cat_expanded` dict で保持、再構築後も維持）
- `page.window.icon` はFlet 0.85開発実行中は効果なし。アイコンはexeビルド時の `--icon` オプションで設定
- `_active` フラグ（`set_active(bool)`）: 他画面遷移時に `False` にして `_page_update()` をスキップ。メイン画面復帰時に `True` に戻す。設定・ブックマーク画面への遷移が詰まる問題の対策
- `loop.call_soon_threadsafe(page.update)` でバックグラウンドスレッドからUI更新（直接 `page.update()` だと asyncio ループが次の操作まで描画を保留する）

### ページネーション
- `PAGE_SIZE = 100`（`main_view.py`）
- `load_articles()` は SQL `LIMIT/OFFSET` で該当ページ分のみ取得
- `count_articles()` でフィルタ条件に一致する総件数を取得、ページ上限を計算
- フィルタ・検索・ソート変更時は `state["page"] = 0` にリセット
- `_update_pagination_ui()` でページナビウィジェット（prev/next ボタン・件数テキスト）を更新

### ソースバッジ色分け（`article_card.py`）
- 1024色パレットを黄金角ハッシュ（φ≈0.618）で生成（`colorsys.hls_to_rgb`）
- `update_source_color_map(ordered_names)` を `rebuild_articles()` の先頭で呼び出し、ソース登録順に重複なく割り当て
- 固定5ソース（Zenn/Qiita/HackerNews/GitHub Trending/DevelopersIO）は従来色を使用しパレット枠を消費しない
- 1024ソース超過分はソース名ハッシュで割り当て（重複あり）

### Flet 0.85 注意事項
- `ft.app()` → `ft.run()`
- `ft.dropdown.Option()` → `ft.DropdownOption(key=, text=)`
- Dropdown `on_change` → `on_select`
- `ft.border.only()` → `ft.Border()`
- `ft.padding.symmetric/all()` → `ft.Padding(left,right,top,bottom)` or int
- `page.on_window_event` → `page.window.on_event`
- `e.data == "close"` → `e.type == ft.WindowEventType.CLOSE`
- `ft.TextButton(content=...)` でテキスト非表示バグ → `ft.GestureDetector` + `ft.Text` で代替

### 取得間隔設定
- デフォルト: 30分
- 最小: 15分、最大: 24時間
- 変更単位: 15分刻み
- 全ソース共通（ソースごとの個別設定なし）
