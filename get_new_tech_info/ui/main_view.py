import asyncio
import flet as ft
from sqlalchemy import select, or_, func
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

def _utc_to_jst_str(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.replace(tzinfo=timezone.utc).astimezone(JST).strftime("%Y/%m/%d %H:%M")

from sqlalchemy import update as sa_update
from ..database import get_session, get_setting, set_setting, get_enabled_sources, get_all_categories, get_all_sources
from ..models import Article, FeedSource
from ..scheduler import fetch_all_sync, fetch_all_sync_with_progress, reschedule, register_on_fetch_done
import threading


def _build_source_dropdown_options() -> list[ft.DropdownOption]:
    """カテゴリ区切り行付きの Dropdown オプションリストを生成する。"""
    options = [ft.DropdownOption(key="すべて", text="すべて")]
    sources = get_enabled_sources()
    cats = get_all_categories()

    # カテゴリID→ソースリスト のマップ
    cat_map: dict[int | None, list] = {None: []}
    for c in cats:
        cat_map[c.id] = []
    for s in sources:
        cat_map.setdefault(s.category_id, []).append(s)

    for c in cats:
        srcs = cat_map.get(c.id, [])
        if not srcs:
            continue
        options.append(ft.DropdownOption(key=f"__cat_{c.id}", text=f"── {c.name} ──", disabled=True))
        for s in srcs:
            options.append(ft.DropdownOption(key=s.name, text=s.name))

    uncategorized = cat_map.get(None, [])
    if uncategorized:
        options.append(ft.DropdownOption(key="__cat_none", text="── 未分類 ──", disabled=True))
        for s in uncategorized:
            options.append(ft.DropdownOption(key=s.name, text=s.name))

    return options


_SORT_ORDERS = {
    "published_desc": lambda: Article.published_at.desc(),
    "published_asc":  lambda: Article.published_at.asc(),
}


PAGE_SIZE = 100


def _get_enabled_source_names_by_category(cat_key: str) -> set[str]:
    """デタッチ問題を避けるため、セッション内で直接DBから取得する。"""
    with get_session() as session:
        if cat_key == "none":
            rows = session.scalars(
                select(FeedSource.name)
                .where(FeedSource.enabled == True, FeedSource.category_id == None)
            ).all()
        else:
            try:
                cat_id = int(cat_key)
                rows = session.scalars(
                    select(FeedSource.name)
                    .where(FeedSource.enabled == True, FeedSource.category_id == cat_id)
                ).all()
            except ValueError:
                rows = []
        return set(rows)


def _build_filter_query(search: str, source: str, read_filter: str, all_enabled):
    enabled_names = {s.name for s in all_enabled}
    q = select(Article)
    if source and source != "すべて":
        if source.startswith("__cat_"):
            cat_key = source[len("__cat_"):]
            cat_names = _get_enabled_source_names_by_category(cat_key)
            q = q.where(Article.source.in_(cat_names))
        else:
            q = q.where(Article.source == source)
    else:
        q = q.where(Article.source.in_(enabled_names))
    if read_filter == "unread":
        q = q.where(Article.is_read == False)
    elif read_filter == "read":
        q = q.where(Article.is_read == True)
    if search:
        kw = f"%{search}%"
        q = q.where(or_(Article.title.ilike(kw), Article.summary.ilike(kw)))
    return q


def count_articles(search: str = "", source: str = "", read_filter: str = "all") -> int:
    all_enabled = get_enabled_sources()
    with get_session() as session:
        q = _build_filter_query(search, source, read_filter, all_enabled)
        return session.scalar(select(func.count()).select_from(q.subquery())) or 0


def load_articles(search: str = "", source: str = "", read_filter: str = "all", sort: str = "published_desc", page: int = 0) -> list[dict]:
    all_enabled = get_enabled_sources()
    with get_session() as session:
        order = _SORT_ORDERS.get(sort, _SORT_ORDERS["published_desc"])()
        q = _build_filter_query(search, source, read_filter, all_enabled)
        q = q.order_by(order).offset(page * PAGE_SIZE).limit(PAGE_SIZE)
        rows = session.scalars(q).all()
        return [
            {
                "id": a.id, "url": a.url, "title": a.title,
                "summary": a.summary, "source": a.source,
                "fetched_at": a.fetched_at, "published_at": a.published_at,
                "is_read": a.is_read, "is_bookmarked": a.is_bookmarked,
            }
            for a in rows
        ]


def load_source_stats() -> dict[str, int]:
    with get_session() as session:
        rows = session.execute(
            select(Article.source, func.count().label("cnt"))
            .where(Article.is_read == False)
            .group_by(Article.source)
        ).all()
        return {r.source: r.cnt for r in rows}


def get_last_fetched() -> str:
    with get_session() as session:
        latest = session.scalar(select(func.max(Article.fetched_at)))
        return _utc_to_jst_str(latest) if latest else "未取得"


def build_main_view(page: ft.Page):
    _loop: list[asyncio.AbstractEventLoop | None] = [None]
    try:
        _loop[0] = asyncio.get_event_loop()
    except Exception:
        pass

    _active = [True]  # False のとき page.update() をスキップ

    def _page_update():
        if not _active[0]:
            return
        loop = _loop[0]
        if loop and loop.is_running():
            loop.call_soon_threadsafe(page.update)
        else:
            page.update()

    def set_active(v: bool):
        _active[0] = v

    _rebuild_lock = threading.Lock()
    _rebuild_again = threading.Event()  # rebuild中に追加リクエストがあったらセット

    def _run_in_bg(fn):
        threading.Thread(target=fn, daemon=True).start()

    # 状態
    state = {
        "view_mode": "card",
        "search": "",
        "source": "すべて",
        "read_filter": "all",
        "sort": "published_desc",
        "delete_target_id": None,
        "selected_ids": set(),
        "page": 0,
        "total": 0,
    }

    # --- コンテンツエリア ---
    article_area = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=8)
    last_fetched_text = ft.Text(f"最終取得: {get_last_fetched()}", size=12, color=ft.Colors.GREY_600)
    loading_ring = ft.ProgressRing(width=16, height=16, visible=False)
    refresh_status_text = ft.Text("", size=11, color=ft.Colors.GREY_600)

    # article_id → {"container": ft.Container, "read_btn": ft.IconButton, "top": ft.Control}
    _article_refs: dict[int, dict] = {}

    # 表示順序管理（SHIFT選択の範囲計算用）
    _article_order: list[int] = []

    # 選択のアンカー（SHIFT選択の起点）
    _select_anchor: list[int | None] = [None]

    # 記事クリック後の clear_selection 誤発火防止フラグ
    _article_just_clicked: list[bool] = [False]

    # --- 未読数表示 ---
    source_stats_col = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO)
    # カテゴリキー → 折りたたみ状態（True=展開）
    _cat_expanded: dict[str, bool] = {}

    def filter_by_source(src: str):
        state["source"] = src
        state["page"] = 0
        # カテゴリキー(__cat_*)はDropdownに存在しないのでDropdownは「すべて」に戻す
        source_dropdown.value = src if not src.startswith("__cat_") else "すべて"
        _rebuild_in_bg()

    def refresh_stats():
        stats = load_source_stats()
        source_stats_col.controls.clear()
        sources = get_enabled_sources()
        cats = get_all_categories()

        cat_map: dict[int | None, list] = {None: []}
        for c in cats:
            cat_map[c.id] = []
        for s in sources:
            cat_map.setdefault(s.category_id, []).append(s)

        def _source_row(src_name: str) -> ft.GestureDetector:
            cnt = stats.get(src_name, 0)
            is_selected = state["source"] == src_name
            return ft.GestureDetector(
                on_tap=lambda e, s=src_name: filter_by_source(s),
                mouse_cursor=ft.MouseCursor.CLICK,
                content=ft.Container(
                    content=ft.Row([
                        ft.Text(
                            src_name, size=12, expand=True,
                            weight=ft.FontWeight.W_600 if is_selected else ft.FontWeight.NORMAL,
                            color=ft.Colors.BLUE_600 if is_selected else ft.Colors.ON_SURFACE,
                        ),
                        ft.Container(
                            content=ft.Text(str(cnt), size=11, color=ft.Colors.WHITE),
                            bgcolor=ft.Colors.BLUE_400,
                            border_radius=10,
                            padding=ft.Padding(left=6, right=6, top=1, bottom=1),
                            visible=cnt > 0,
                        ),
                    ], spacing=4),
                    padding=ft.Padding(left=20, right=4, top=2, bottom=2),
                    border_radius=4,
                    bgcolor=ft.Colors.BLUE_50 if is_selected else None,
                ),
            )

        def _add_category_section(cat_key: int | str, cat_name: str, srcs: list):
            if not srcs:
                return
            filter_key = f"__cat_{cat_key}"
            is_cat_selected = state["source"] == filter_key
            total_unread = sum(stats.get(s.name, 0) for s in srcs)
            # 初回は展開状態
            expanded = _cat_expanded.get(filter_key, True)

            def _toggle_expand(e, fk=filter_key):
                _cat_expanded[fk] = not _cat_expanded.get(fk, True)
                refresh_stats()
                _page_update()

            header = ft.GestureDetector(
                on_tap=lambda e, k=cat_key: filter_by_source(f"__cat_{k}"),
                mouse_cursor=ft.MouseCursor.CLICK,
                content=ft.Container(
                    content=ft.Row([
                        ft.GestureDetector(
                            content=ft.Icon(
                                ft.Icons.EXPAND_MORE if expanded else ft.Icons.CHEVRON_RIGHT,
                                size=14,
                                color=ft.Colors.GREY_600,
                            ),
                            on_tap=_toggle_expand,
                            mouse_cursor=ft.MouseCursor.CLICK,
                        ),
                        ft.Text(
                            cat_name, size=11, expand=True,
                            color=ft.Colors.BLUE_600 if is_cat_selected else ft.Colors.GREY_700,
                            weight=ft.FontWeight.W_600,
                        ),
                        ft.Container(
                            content=ft.Text(str(total_unread), size=10, color=ft.Colors.WHITE),
                            bgcolor=ft.Colors.BLUE_400,
                            border_radius=10,
                            padding=ft.Padding(left=6, right=6, top=1, bottom=1),
                            visible=total_unread > 0,
                        ),
                    ], spacing=2),
                    padding=ft.Padding(left=4, right=4, top=4, bottom=4),
                    border_radius=4,
                    bgcolor=ft.Colors.BLUE_50 if is_cat_selected else None,
                ),
            )
            source_stats_col.controls.append(header)

            if expanded:
                for s in srcs:
                    source_stats_col.controls.append(_source_row(s.name))

        for c in cats:
            _add_category_section(c.id, c.name, cat_map.get(c.id, []))

        uncategorized = cat_map.get(None, [])
        _add_category_section("none", "未分類", uncategorized)

    def _build_article_controls(articles: list[dict]):
        from .article_card import article_card, article_row
        _article_refs.clear()
        article_area.controls.clear()
        _article_order.clear()
        _article_order.extend(a["id"] for a in articles)
        if state["view_mode"] == "card":
            row_items = []
            for a in articles:
                sel = a["id"] in state["selected_ids"]
                card, inner, read_btn = article_card(a, on_read=mark_read, on_bookmark=toggle_bookmark,
                                                     on_delete=confirm_delete, on_mark_read_only=mark_read_only,
                                                     on_select=toggle_select, is_selected=sel)
                wrapper = ft.Container(content=card, expand=True)
                _article_refs[a["id"]] = {"container": inner, "read_btn": read_btn, "top": wrapper}
                row_items.append(wrapper)
                if len(row_items) == 3:
                    article_area.controls.append(ft.Row(row_items, spacing=8, vertical_alignment=ft.CrossAxisAlignment.START))
                    row_items = []
            if row_items:
                while len(row_items) < 3:
                    row_items.append(ft.Container(expand=True))
                article_area.controls.append(ft.Row(row_items, spacing=8, vertical_alignment=ft.CrossAxisAlignment.START))
        else:
            for a in articles:
                sel = a["id"] in state["selected_ids"]
                outer, _, read_btn = article_row(a, on_read=mark_read, on_bookmark=toggle_bookmark,
                                                 on_delete=confirm_delete, on_mark_read_only=mark_read_only,
                                                 on_select=toggle_select, is_selected=sel)
                _article_refs[a["id"]] = {"container": outer, "read_btn": read_btn, "top": outer}
                article_area.controls.append(outer)

    def rebuild_articles():
        """記事一覧・サイドバー・Dropdownを全更新する。ロックで排他、実行中の追加要求は1件に集約。"""
        if not _rebuild_lock.acquire(blocking=False):
            _rebuild_again.set()
            return
        try:
            while True:
                _rebuild_again.clear()
                from .article_card import update_source_color_map
                all_sources = get_all_sources()
                update_source_color_map([s.name for s in all_sources])
                total = count_articles(state["search"], state["source"], state["read_filter"])
                state["total"] = total
                max_page = max(0, (total - 1) // PAGE_SIZE) if total > 0 else 0
                if state["page"] > max_page:
                    state["page"] = max_page
                articles = load_articles(state["search"], state["source"], state["read_filter"], state["sort"], state["page"])
                _build_article_controls(articles)
                bulk_read_btn.visible = len(state["selected_ids"]) > 0
                bulk_read_btn.text = f"選択済み {len(state['selected_ids'])} 件を既読"
                _update_pagination_ui()
                _page_update()
                source_dropdown.options = _build_source_dropdown_options()
                refresh_stats()
                last_fetched_text.value = f"最終取得: {get_last_fetched()}"
                _page_update()
                if not _rebuild_again.is_set():
                    break
        finally:
            _rebuild_lock.release()

    def _rebuild_in_bg():
        _run_in_bg(rebuild_articles)

    # register UI refresh callback for scheduler
    register_on_fetch_done(rebuild_articles)

    # --- イベントハンドラ ---
    def _mark_read_async(article_id: int):
        state["selected_ids"].discard(article_id)
        ref = _article_refs.get(article_id)
        if ref:
            if state["read_filter"] == "unread":
                ref["top"].visible = False
            else:
                ref["container"].opacity = 0.55
                ref["container"].bgcolor = None
                ref["read_btn"].visible = False
            _page_update()
        def _do():
            with get_session() as session:
                session.execute(
                    sa_update(Article).where(Article.id == article_id).values(is_read=True)
                )
                session.commit()
            rebuild_articles()
        threading.Thread(target=_do, daemon=True).start()

    def mark_read(article_id: int):
        _mark_read_async(article_id)

    def mark_read_only(article_id: int):
        _mark_read_async(article_id)

    def toggle_select(article_id: int):
        _article_just_clicked[0] = True  # GestureDetector への誤伝播を防ぐ

        anchor = _select_anchor[0]
        if anchor is None:
            # 1回目クリック: 前の選択を全クリアしてから起点を記憶
            for aid in list(state["selected_ids"]):
                ref = _article_refs.get(aid)
                if ref:
                    ref["container"].bgcolor = None
            state["selected_ids"].clear()
            _select_anchor[0] = article_id
            state["selected_ids"] = {article_id}
            ref = _article_refs.get(article_id)
            if ref:
                ref["container"].bgcolor = ft.Colors.BLUE_50
        elif anchor == article_id:
            # 同じ記事を再クリック: 選択解除
            _select_anchor[0] = None
            state["selected_ids"].clear()
            ref = _article_refs.get(article_id)
            if ref:
                ref["container"].bgcolor = None
        else:
            # 2回目クリック: 起点〜終点の範囲を選択
            anchor_idx = _article_order.index(anchor) if anchor in _article_order else -1
            click_idx = _article_order.index(article_id) if article_id in _article_order else -1
            if anchor_idx >= 0 and click_idx >= 0:
                # 前回の範囲選択をクリアしてから新たな範囲を設定
                for aid in list(state["selected_ids"]):
                    ref = _article_refs.get(aid)
                    if ref:
                        ref["container"].bgcolor = None
                state["selected_ids"].clear()
                lo, hi = sorted([anchor_idx, click_idx])
                for aid in _article_order[lo:hi + 1]:
                    state["selected_ids"].add(aid)
                    ref = _article_refs.get(aid)
                    if ref:
                        ref["container"].bgcolor = ft.Colors.BLUE_50
            # 次クリックで新たな起点として扱うためアンカーをリセット
            _select_anchor[0] = None

        has_sel = len(state["selected_ids"]) > 0
        bulk_read_btn.visible = has_sel
        bulk_read_btn.text = f"選択済み {len(state['selected_ids'])} 件を既読"
        _page_update()

    def clear_selection():
        # 記事クリック直後はバブルアップによる誤発火なので無視
        if _article_just_clicked[0]:
            _article_just_clicked[0] = False
            return
        if not state["selected_ids"]:
            return
        for aid in list(state["selected_ids"]):
            ref = _article_refs.get(aid)
            if ref:
                ref["container"].bgcolor = None
        state["selected_ids"].clear()
        _select_anchor[0] = None
        bulk_read_btn.visible = False
        _page_update()

    def mark_selected_read(e):
        ids = list(state["selected_ids"])
        if not ids:
            return
        state["selected_ids"].clear()
        bulk_read_btn.visible = False
        for aid in ids:
            ref = _article_refs.get(aid)
            if ref:
                if state["read_filter"] == "unread":
                    ref["top"].visible = False
                else:
                    ref["container"].opacity = 0.55
                    ref["container"].bgcolor = None
                    ref["read_btn"].visible = False
        _page_update()
        # バックグラウンドでDB更新 → 全再構築
        def _do():
            with get_session() as session:
                session.execute(
                    sa_update(Article).where(Article.id.in_(ids)).values(is_read=True)
                )
                session.commit()
            rebuild_articles()
        threading.Thread(target=_do, daemon=True).start()

    def toggle_bookmark(article_id: int):
        max_bm = int(get_setting("max_bookmarks"))
        with get_session() as session:
            a = session.get(Article, article_id)
            if not a:
                return
            if not a.is_bookmarked:
                # ソースが属するカテゴリのブックマーク件数で上限チェック
                fs = session.scalar(select(FeedSource).where(FeedSource.name == a.source))
                cat_id = fs.category_id if fs else None
                if cat_id is not None:
                    count = session.scalar(
                        select(func.count()).select_from(Article)
                        .join(FeedSource, FeedSource.name == Article.source)
                        .where(Article.is_bookmarked == True, FeedSource.category_id == cat_id)
                    )
                else:
                    count = session.scalar(
                        select(func.count()).select_from(Article)
                        .outerjoin(FeedSource, FeedSource.name == Article.source)
                        .where(Article.is_bookmarked == True, FeedSource.category_id == None)
                    )
                if count >= max_bm:
                    show_bookmark_limit_dialog()
                    return
            a.is_bookmarked = not a.is_bookmarked
            session.commit()
        _rebuild_in_bg()

    def confirm_delete(article_id: int):
        state["delete_target_id"] = article_id
        delete_dialog.open = True
        _page_update()

    def execute_delete(e):
        with get_session() as session:
            a = session.get(Article, state["delete_target_id"])
            if a:
                session.delete(a)
                session.commit()
        delete_dialog.open = False
        state["delete_target_id"] = None
        _rebuild_in_bg()

    def cancel_delete(e):
        delete_dialog.open = False
        _page_update()

    def show_bookmark_limit_dialog():
        bookmark_limit_dialog.open = True
        _page_update()

    def close_bookmark_limit(e):
        bookmark_limit_dialog.open = False
        _page_update()

    _refresh_state = {"running": False}
    _status_clear_timer: list[threading.Timer] = []

    def _set_refresh_status(msg: str, color):
        if _status_clear_timer:
            _status_clear_timer[0].cancel()
            _status_clear_timer.clear()
        refresh_status_text.value = msg
        refresh_status_text.color = color
        _page_update()
        t = threading.Timer(5.0, _clear_refresh_status)
        _status_clear_timer.append(t)
        t.start()

    def _clear_refresh_status():
        refresh_status_text.value = ""
        _page_update()

    def _set_fetching(fetching: bool):
        _refresh_state["running"] = fetching
        refresh_btn.disabled = fetching
        refresh_btn.text = "リフレッシュ" if not fetching else "取得中..."
        refresh_btn.icon = ft.Icons.HOURGLASS_TOP if fetching else ft.Icons.REFRESH
        loading_ring.visible = fetching
        _page_update()


    def on_refresh(e):
        if _refresh_state["running"]:
            return
        _set_fetching(True)
        threading.Thread(target=_do_refresh, daemon=True).start()

    def _do_refresh():
        try:
            def on_progress(done, total, source_name, ok):
                mark = "✓" if ok else "✗"
                refresh_status_text.value = f"{mark} {source_name} ({done}/{total})"
                refresh_status_text.color = ft.Colors.GREEN_600 if ok else ft.Colors.RED_400
                _page_update()

            new_count, source_results = fetch_all_sync_with_progress(on_progress)
            rebuild_articles()
            _set_fetching(False)

            failed = [r["source"] for r in source_results if not r["ok"]]
            if failed:
                msg = f"✓ {new_count}件新着  ✗ 失敗: {', '.join(failed)}"
                color = ft.Colors.ORANGE_700
            elif new_count > 0:
                msg = f"✓ {new_count}件 新着"
                color = ft.Colors.GREEN_600
            else:
                msg = "✓ 新着なし"
                color = ft.Colors.GREY_500
            _set_refresh_status(msg, color)
        except Exception:
            rebuild_articles()
            _set_fetching(False)
            _set_refresh_status("✗ 取得失敗", ft.Colors.RED_400)

    _search_timer: list[threading.Timer] = []

    def on_search(e):
        state["search"] = e.control.value
        state["page"] = 0
        if _search_timer:
            _search_timer[0].cancel()
            _search_timer.clear()
        t = threading.Timer(0.3, rebuild_articles)
        _search_timer.append(t)
        t.start()

    def on_source_change(e):
        v = e.control.value or ""
        if v.startswith("__cat_"):
            source_dropdown.value = state["source"]
            _page_update()
            return
        state["source"] = v
        state["page"] = 0
        _rebuild_in_bg()

    source_dropdown = ft.Dropdown(
        options=_build_source_dropdown_options(),
        value="すべて",
        on_select=on_source_change,
        dense=True,
    )

    def on_read_filter_change(e):
        state["read_filter"] = e.control.value
        state["page"] = 0
        _rebuild_in_bg()

    def on_sort_change(e):
        state["sort"] = e.control.value
        state["page"] = 0
        _rebuild_in_bg()

    def on_view_card(e):
        state["view_mode"] = "card"
        _rebuild_in_bg()

    def on_view_list(e):
        state["view_mode"] = "list"
        _rebuild_in_bg()

    async def go_bookmarks(e):
        await page.push_route("/bookmarks")

    async def go_settings(e):
        await page.push_route("/settings")

    # --- ダイアログ ---
    delete_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("記事を削除しますか？"),
        content=ft.Text("この操作は取り消せません。"),
        actions=[
            ft.TextButton("キャンセル", on_click=cancel_delete),
            ft.ElevatedButton("削除", color=ft.Colors.WHITE, bgcolor=ft.Colors.RED_400, on_click=execute_delete),
        ],
    )
    bookmark_limit_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("ブックマーク上限に達しました"),
        content=ft.Text("不要なブックマークを削除してから追加してください。"),
        actions=[ft.TextButton("閉じる", on_click=close_bookmark_limit)],
    )
    page.overlay.extend([delete_dialog, bookmark_limit_dialog])

    # --- ページネーション ---
    pagination_text = ft.Text("", size=12, color=ft.Colors.GREY_600)
    pagination_prev = ft.IconButton(
        icon=ft.Icons.CHEVRON_LEFT,
        tooltip="前のページ",
        disabled=True,
        on_click=lambda e: _go_page(state["page"] - 1),
    )
    pagination_next = ft.IconButton(
        icon=ft.Icons.CHEVRON_RIGHT,
        tooltip="次のページ",
        disabled=True,
        on_click=lambda e: _go_page(state["page"] + 1),
    )
    pagination_row = ft.Row(
        [pagination_prev, pagination_text, pagination_next],
        spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        visible=False,
    )

    def _go_page(p: int):
        max_page = max(0, (state["total"] - 1) // PAGE_SIZE) if state["total"] > 0 else 0
        state["page"] = max(0, min(p, max_page))
        _rebuild_in_bg()

    def _update_pagination_ui():
        total = state["total"]
        p = state["page"]
        max_page = max(0, (total - 1) // PAGE_SIZE) if total > 0 else 0
        start = p * PAGE_SIZE + 1
        end = min((p + 1) * PAGE_SIZE, total)
        pagination_row.visible = total > PAGE_SIZE
        if total > 0:
            pagination_text.value = f"{start}–{end} / {total}件"
        else:
            pagination_text.value = "0件"
        pagination_prev.disabled = (p <= 0)
        pagination_next.disabled = (p >= max_page)

    refresh_btn = ft.ElevatedButton(
        "リフレッシュ",
        icon=ft.Icons.REFRESH,
        on_click=on_refresh,
        width=196,
    )

    # --- サイドバー ---
    sidebar = ft.Container(
        content=ft.Column(
            [
                ft.Text("にゅーす・あつめーる", size=18, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                ft.Text("ソース", size=12, color=ft.Colors.GREY_600, weight=ft.FontWeight.W_600),
                source_dropdown,
                ft.RadioGroup(
                    value="all",
                    on_change=on_read_filter_change,
                    content=ft.Column([
                        ft.Radio(value="all",    label="すべて", height=28),
                        ft.Radio(value="unread", label="未読のみ", height=28),
                        ft.Radio(value="read",   label="既読のみ", height=28),
                    ], spacing=0),
                ),
                ft.Divider(),
                ft.Text("未読数", size=12, color=ft.Colors.GREY_600, weight=ft.FontWeight.W_600),
                ft.Container(
                    content=source_stats_col,
                    height=200,
                    border=ft.Border(
                        top=ft.BorderSide(1, ft.Colors.GREY_200),
                        bottom=ft.BorderSide(1, ft.Colors.GREY_200),
                    ),
                    padding=ft.Padding(left=0, right=0, top=4, bottom=4),
                ),
                ft.Divider(),
                ft.ElevatedButton("ブックマーク", icon=ft.Icons.BOOKMARK, on_click=go_bookmarks, width=196),
                ft.ElevatedButton("設定", icon=ft.Icons.SETTINGS, on_click=go_settings, width=196),
                ft.Divider(),
                last_fetched_text,
                refresh_btn,
                ft.Row([loading_ring, refresh_status_text], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        ),
        width=220,
        padding=12,
        border=ft.Border(right=ft.BorderSide(1, ft.Colors.GREY_200)),
    )

    bulk_read_btn = ft.ElevatedButton(
        "選択済みを既読",
        icon=ft.Icons.MARK_EMAIL_READ_OUTLINED,
        on_click=mark_selected_read,
        visible=False,
        bgcolor=ft.Colors.BLUE_400,
        color=ft.Colors.WHITE,
    )

    # --- ツールバー ---
    toolbar = ft.Container(
        content=ft.Row(
            [
                ft.TextField(
                    hint_text="キーワード検索...",
                    on_change=on_search,
                    expand=True,
                    dense=True,
                    height=38,
                ),
                ft.Container(width=8),
                bulk_read_btn,
                ft.Container(width=4),
                ft.Dropdown(
                    options=[
                        ft.DropdownOption(key="published_desc", text="公開日 ↓"),
                        ft.DropdownOption(key="published_asc",  text="公開日 ↑"),
                    ],
                    value="published_desc",
                    on_select=on_sort_change,
                    dense=True,
                    width=130,
                ),
                ft.IconButton(ft.Icons.GRID_VIEW, tooltip="カード表示", on_click=on_view_card),
                ft.IconButton(ft.Icons.LIST, tooltip="リスト表示", on_click=on_view_list),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding(left=12, right=12, top=6, bottom=6),
        border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.GREY_200)),
    )

    # --- メインレイアウト ---
    content_col = ft.Column(
        [
            toolbar,
            ft.GestureDetector(
                content=ft.Container(content=article_area, expand=True, padding=12),
                on_tap=lambda e: clear_selection(),
                expand=True,
            ),
            ft.Container(
                content=pagination_row,
                padding=ft.Padding(left=12, right=12, top=4, bottom=8),
                border=ft.Border(top=ft.BorderSide(1, ft.Colors.GREY_200)),
            ),
        ],
        expand=True,
        spacing=0,
    )

    view = ft.Row(
        [sidebar, ft.VerticalDivider(width=1), content_col],
        expand=True,
        spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )

    rebuild_articles()
    return view, rebuild_articles, set_active
