import asyncio
import threading
import flet as ft
from sqlalchemy import select, func, or_

from ..database import get_session, get_setting
from ..models import Article, FeedSource, SourceCategory


PAGE_SIZE = 100

_SORT_ORDERS = {
    "published_desc": lambda: Article.published_at.desc(),
    "published_asc":  lambda: Article.published_at.asc(),
}


def _bm_filter_query(search: str, cat_filter: str):
    q = select(Article).where(Article.is_bookmarked == True)
    if cat_filter and cat_filter != "すべて":
        if cat_filter == "__cat_none":
            q = q.outerjoin(FeedSource, FeedSource.name == Article.source).where(FeedSource.category_id == None)
        elif cat_filter.startswith("__cat_"):
            try:
                cat_id = int(cat_filter[len("__cat_"):])
                q = (q.join(FeedSource, FeedSource.name == Article.source)
                       .where(FeedSource.category_id == cat_id))
            except ValueError:
                pass
        else:
            q = q.where(Article.source == cat_filter)
    if search:
        kw = f"%{search}%"
        q = q.where(or_(Article.title.ilike(kw), Article.summary.ilike(kw)))
    return q


def build_bookmarks_view(page: ft.Page) -> tuple:
    _loop: list = [None]
    try:
        _loop[0] = asyncio.get_event_loop()
    except Exception:
        pass
    _active = [True]

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
    _rebuild_again = threading.Event()

    state = {
        "view_mode": "card",
        "search": "",
        "cat_filter": "すべて",
        "sort": "published_desc",
        "delete_target_id": None,
        "page": 0,
        "total": 0,
        "selected_ids": set(),
    }

    article_area = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=8)
    stats_col = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO)
    _cat_expanded: dict[str, bool] = {}
    _article_refs: dict[int, dict] = {}
    _article_order: list[int] = []
    _select_anchor: list[int | None] = [None]
    _article_just_clicked: list[bool] = [False]

    # --- 選択操作 ---
    def toggle_select(article_id: int):
        _article_just_clicked[0] = True

        anchor = _select_anchor[0]
        if anchor is None:
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
            _select_anchor[0] = None
            state["selected_ids"].clear()
            ref = _article_refs.get(article_id)
            if ref:
                ref["container"].bgcolor = None
        else:
            anchor_idx = _article_order.index(anchor) if anchor in _article_order else -1
            click_idx = _article_order.index(article_id) if article_id in _article_order else -1
            if anchor_idx >= 0 and click_idx >= 0:
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
            _select_anchor[0] = None

        has_sel = len(state["selected_ids"]) > 0
        bulk_delete_btn.visible = has_sel
        bulk_delete_btn.text = f"選択済み {len(state['selected_ids'])} 件を削除"
        _page_update()

    def clear_selection():
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
        bulk_delete_btn.visible = False
        _page_update()

    # --- サイドバー：カテゴリ別ブックマーク件数 ---
    def _refresh_stats():
        with get_session() as session:
            src_rows = session.execute(
                select(Article.source, func.count().label("cnt"))
                .where(Article.is_bookmarked == True)
                .group_by(Article.source)
            ).all()
            src_counts: dict[str, int] = {r.source: r.cnt for r in src_rows}

            cat_counts: dict[int | None, int] = {}
            fs_rows = session.execute(
                select(FeedSource.name, FeedSource.category_id, FeedSource.display_order)
                .order_by(FeedSource.display_order, FeedSource.id)
            ).all()
            cat_srcs: dict[int | None, list[str]] = {None: []}
            for r in fs_rows:
                cat_srcs.setdefault(r.category_id, []).append(r.name)
                cat_counts[r.category_id] = cat_counts.get(r.category_id, 0) + src_counts.get(r.name, 0)

            cats = session.scalars(
                select(SourceCategory).order_by(SourceCategory.display_order, SourceCategory.id)
            ).all()
            cat_info = [(c.id, c.name) for c in cats]

        stats_col.controls.clear()

        is_all = state["cat_filter"] == "すべて"
        total_bm = sum(src_counts.values())
        stats_col.controls.append(
            ft.GestureDetector(
                on_tap=lambda e: _filter_cat("すべて"),
                mouse_cursor=ft.MouseCursor.CLICK,
                content=ft.Container(
                    content=ft.Row([
                        ft.Text("すべて", size=12, expand=True,
                                weight=ft.FontWeight.W_600 if is_all else ft.FontWeight.NORMAL,
                                color=ft.Colors.BLUE_600 if is_all else ft.Colors.ON_SURFACE),
                        ft.Container(
                            content=ft.Text(str(total_bm), size=11, color=ft.Colors.WHITE),
                            bgcolor=ft.Colors.BLUE_400, border_radius=10,
                            padding=ft.Padding(left=6, right=6, top=1, bottom=1),
                            visible=total_bm > 0,
                        ),
                    ], spacing=4),
                    padding=ft.Padding(left=4, right=4, top=3, bottom=3),
                    border_radius=4,
                    bgcolor=ft.Colors.BLUE_50 if is_all else None,
                ),
            )
        )

        def _source_row(src_name: str) -> ft.GestureDetector:
            cnt = src_counts.get(src_name, 0)
            is_sel = state["cat_filter"] == src_name
            return ft.GestureDetector(
                on_tap=lambda e, s=src_name: _filter_src(s),
                mouse_cursor=ft.MouseCursor.CLICK,
                content=ft.Container(
                    content=ft.Row([
                        ft.Text(src_name, size=12, expand=True,
                                weight=ft.FontWeight.W_600 if is_sel else ft.FontWeight.NORMAL,
                                color=ft.Colors.BLUE_600 if is_sel else ft.Colors.ON_SURFACE),
                        ft.Container(
                            content=ft.Text(str(cnt), size=11, color=ft.Colors.WHITE),
                            bgcolor=ft.Colors.BLUE_400, border_radius=10,
                            padding=ft.Padding(left=6, right=6, top=1, bottom=1),
                            visible=cnt > 0,
                        ),
                    ], spacing=4),
                    padding=ft.Padding(left=20, right=4, top=2, bottom=2),
                    border_radius=4,
                    bgcolor=ft.Colors.BLUE_50 if is_sel else None,
                ),
            )

        def _cat_row(cat_id: int, cat_name: str):
            filter_key = f"__cat_{cat_id}"
            is_sel = state["cat_filter"] == filter_key
            cnt = cat_counts.get(cat_id, 0)
            srcs = cat_srcs.get(cat_id, [])
            expanded = _cat_expanded.get(filter_key, True)

            def _toggle(e, fk=filter_key):
                _cat_expanded[fk] = not _cat_expanded.get(fk, True)
                _refresh_stats()
                _page_update()

            header = ft.GestureDetector(
                on_tap=lambda e, fk=filter_key: _filter_cat(fk),
                mouse_cursor=ft.MouseCursor.CLICK,
                content=ft.Container(
                    content=ft.Row([
                        ft.GestureDetector(
                            content=ft.Icon(
                                ft.Icons.EXPAND_MORE if expanded else ft.Icons.CHEVRON_RIGHT,
                                size=14, color=ft.Colors.GREY_600,
                            ),
                            on_tap=_toggle,
                            mouse_cursor=ft.MouseCursor.CLICK,
                        ),
                        ft.Text(cat_name, size=11, expand=True,
                                color=ft.Colors.BLUE_600 if is_sel else ft.Colors.GREY_700,
                                weight=ft.FontWeight.W_600),
                        ft.Container(
                            content=ft.Text(str(cnt), size=10, color=ft.Colors.WHITE),
                            bgcolor=ft.Colors.BLUE_400, border_radius=10,
                            padding=ft.Padding(left=6, right=6, top=1, bottom=1),
                            visible=cnt > 0,
                        ),
                    ], spacing=2),
                    padding=ft.Padding(left=4, right=4, top=4, bottom=4),
                    border_radius=4,
                    bgcolor=ft.Colors.BLUE_50 if is_sel else None,
                ),
            )
            stats_col.controls.append(header)
            if expanded:
                for src_name in srcs:
                    if src_counts.get(src_name, 0) > 0:
                        stats_col.controls.append(_source_row(src_name))

        for cat_id, cat_name in cat_info:
            _cat_row(cat_id, cat_name)

        none_key = "__cat_none"
        if cat_counts.get(None, 0) > 0:
            is_none_sel = state["cat_filter"] == none_key
            none_cnt = cat_counts[None]
            stats_col.controls.append(
                ft.GestureDetector(
                    on_tap=lambda e: _filter_cat(none_key),
                    mouse_cursor=ft.MouseCursor.CLICK,
                    content=ft.Container(
                        content=ft.Row([
                            ft.Text("未分類", size=11, expand=True,
                                    color=ft.Colors.BLUE_600 if is_none_sel else ft.Colors.GREY_700,
                                    weight=ft.FontWeight.W_600),
                            ft.Container(
                                content=ft.Text(str(none_cnt), size=10, color=ft.Colors.WHITE),
                                bgcolor=ft.Colors.BLUE_400, border_radius=10,
                                padding=ft.Padding(left=6, right=6, top=1, bottom=1),
                                visible=none_cnt > 0,
                            ),
                        ], spacing=2),
                        padding=ft.Padding(left=4, right=4, top=4, bottom=4),
                        border_radius=4,
                        bgcolor=ft.Colors.BLUE_50 if is_none_sel else None,
                    ),
                )
            )

    def _filter_cat(key: str):
        state["cat_filter"] = key
        state["page"] = 0
        _rebuild_in_bg()

    def _filter_src(src_name: str):
        state["cat_filter"] = src_name
        state["page"] = 0
        _rebuild_in_bg()

    # --- 記事再構築 ---
    def rebuild():
        if not _rebuild_lock.acquire(blocking=False):
            _rebuild_again.set()
            return
        try:
            while True:
                _rebuild_again.clear()
                from .article_card import article_card, article_row, update_source_color_map

                with get_session() as session:
                    q = _bm_filter_query(state["search"], state["cat_filter"])
                    total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
                    state["total"] = total
                    max_page = max(0, (total - 1) // PAGE_SIZE) if total > 0 else 0
                    if state["page"] > max_page:
                        state["page"] = max_page
                    order_fn = _SORT_ORDERS.get(state["sort"], _SORT_ORDERS["published_desc"])()
                    rows = session.scalars(
                        q.order_by(order_fn).offset(state["page"] * PAGE_SIZE).limit(PAGE_SIZE)
                    ).all()
                    articles = [
                        {
                            "id": a.id, "url": a.url, "title": a.title,
                            "summary": a.summary, "source": a.source,
                            "fetched_at": a.fetched_at, "published_at": a.published_at,
                            "is_read": a.is_read, "is_bookmarked": a.is_bookmarked,
                        }
                        for a in rows
                    ]
                    all_sources = session.scalars(select(FeedSource)).all()
                    source_names = [s.name for s in all_sources]

                update_source_color_map(source_names)
                _article_refs.clear()
                _article_order.clear()
                _article_order.extend(a["id"] for a in articles)
                article_area.controls.clear()

                if not articles:
                    article_area.controls.append(
                        ft.Container(
                            content=ft.Text("ブックマークはありません", color=ft.Colors.GREY_500),
                            padding=16,
                        )
                    )
                elif state["view_mode"] == "card":
                    row_items = []
                    for a in articles:
                        sel = a["id"] in state["selected_ids"]
                        card, inner, _ = article_card(
                            a, on_read=mark_read, on_bookmark=toggle_bookmark,
                            on_delete=confirm_delete, on_mark_read_only=mark_read,
                            on_select=toggle_select, is_selected=sel,
                        )
                        wrapper = ft.Container(content=card, expand=True)
                        _article_refs[a["id"]] = {"container": inner, "top": wrapper}
                        row_items.append(wrapper)
                        if len(row_items) == 3:
                            article_area.controls.append(
                                ft.Row(row_items, spacing=8, vertical_alignment=ft.CrossAxisAlignment.START)
                            )
                            row_items = []
                    if row_items:
                        while len(row_items) < 3:
                            row_items.append(ft.Container(expand=True))
                        article_area.controls.append(
                            ft.Row(row_items, spacing=8, vertical_alignment=ft.CrossAxisAlignment.START)
                        )
                else:
                    for a in articles:
                        sel = a["id"] in state["selected_ids"]
                        outer, inner, _ = article_row(
                            a, on_read=mark_read, on_bookmark=toggle_bookmark,
                            on_delete=confirm_delete, on_mark_read_only=mark_read,
                            on_select=toggle_select, is_selected=sel,
                        )
                        _article_refs[a["id"]] = {"container": inner, "top": outer}
                        article_area.controls.append(outer)

                bulk_delete_btn.visible = len(state["selected_ids"]) > 0
                bulk_delete_btn.text = f"選択済み {len(state['selected_ids'])} 件を削除"
                _update_pagination_ui()
                _refresh_stats()
                _page_update()
                if not _rebuild_again.is_set():
                    break
        finally:
            _rebuild_lock.release()

    def _rebuild_in_bg():
        threading.Thread(target=rebuild, daemon=True).start()

    # --- ページネーション ---
    pagination_text = ft.Text("", size=12, color=ft.Colors.GREY_600)
    pagination_prev = ft.IconButton(
        icon=ft.Icons.CHEVRON_LEFT, tooltip="前のページ", disabled=True,
        on_click=lambda e: _go_page(state["page"] - 1),
    )
    pagination_next = ft.IconButton(
        icon=ft.Icons.CHEVRON_RIGHT, tooltip="次のページ", disabled=True,
        on_click=lambda e: _go_page(state["page"] + 1),
    )
    pagination_row = ft.Row(
        [pagination_prev, pagination_text, pagination_next],
        spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER, visible=False,
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
        pagination_text.value = f"{start}–{end} / {total}件" if total > 0 else "0件"
        pagination_prev.disabled = (p <= 0)
        pagination_next.disabled = (p >= max_page)

    # --- イベントハンドラ ---
    def mark_read(article_id: int):
        with get_session() as session:
            a = session.get(Article, article_id)
            if a:
                a.is_read = True
                session.commit()
        _rebuild_in_bg()

    def toggle_bookmark(article_id: int):
        max_bm = int(get_setting("max_bookmarks"))
        with get_session() as session:
            a = session.get(Article, article_id)
            if not a:
                return
            if not a.is_bookmarked:
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
                    bookmark_limit_dialog.open = True
                    _page_update()
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

    def delete_selected(e):
        ids = list(state["selected_ids"])
        if not ids:
            return
        bulk_delete_confirm_dialog.open = True
        _page_update()

    def execute_bulk_delete(e):
        ids = list(state["selected_ids"])
        bulk_delete_confirm_dialog.open = False
        state["selected_ids"].clear()
        _select_anchor[0] = None
        with get_session() as session:
            rows = session.scalars(select(Article).where(Article.id.in_(ids))).all()
            for a in rows:
                session.delete(a)
            session.commit()
        _rebuild_in_bg()

    def cancel_bulk_delete(e):
        bulk_delete_confirm_dialog.open = False
        _page_update()

    _search_timer: list[threading.Timer] = []

    def on_search(e):
        state["search"] = e.control.value
        state["page"] = 0
        if _search_timer:
            _search_timer[0].cancel()
            _search_timer.clear()
        t = threading.Timer(0.3, rebuild)
        _search_timer.append(t)
        t.start()

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

    async def go_back(e):
        await page.push_route("/")

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
    bulk_delete_confirm_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("選択した記事を削除しますか？"),
        content=ft.Text("この操作は取り消せません。"),
        actions=[
            ft.TextButton("キャンセル", on_click=cancel_bulk_delete),
            ft.ElevatedButton("削除", color=ft.Colors.WHITE, bgcolor=ft.Colors.RED_400, on_click=execute_bulk_delete),
        ],
    )
    bookmark_limit_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("ブックマーク上限に達しました"),
        content=ft.Text("不要なブックマークを削除してから追加してください。"),
        actions=[ft.TextButton("閉じる", on_click=lambda e: (setattr(bookmark_limit_dialog, "open", False), _page_update()))],
    )
    page.overlay.extend([delete_dialog, bulk_delete_confirm_dialog, bookmark_limit_dialog])

    # --- 一括削除ボタン（ツールバーに入れるため先に宣言） ---
    bulk_delete_btn = ft.ElevatedButton(
        "選択済みを削除",
        icon=ft.Icons.DELETE_SWEEP,
        on_click=delete_selected,
        visible=False,
        bgcolor=ft.Colors.RED_400,
        color=ft.Colors.WHITE,
    )

    # --- サイドバー ---
    sidebar = ft.Container(
        content=ft.Column(
            [
                ft.Row([
                    ft.IconButton(ft.Icons.ARROW_BACK, tooltip="メインに戻る", on_click=go_back),
                    ft.Text("ブックマーク", size=16, weight=ft.FontWeight.BOLD),
                ], spacing=4),
                ft.Divider(),
                ft.Text("カテゴリ", size=12, color=ft.Colors.GREY_600, weight=ft.FontWeight.W_600),
                ft.Container(
                    content=stats_col,
                    height=280,
                    border=ft.Border(
                        top=ft.BorderSide(1, ft.Colors.GREY_200),
                        bottom=ft.BorderSide(1, ft.Colors.GREY_200),
                    ),
                    padding=ft.Padding(left=0, right=0, top=4, bottom=4),
                ),
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        ),
        width=220,
        padding=12,
        border=ft.Border(right=ft.BorderSide(1, ft.Colors.GREY_200)),
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
                bulk_delete_btn,
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

    rebuild()
    return view, set_active
