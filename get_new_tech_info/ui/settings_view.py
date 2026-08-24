import flet as ft
from ..database import (
    get_setting, set_setting,
    get_all_sources, add_rss_source, toggle_source, delete_source, assign_category,
    swap_source_order, get_all_categories, add_category, delete_category, rename_category,
    swap_category_order, update_source_url,
)
from ..scheduler import reschedule

INTERVAL_OPTIONS = list(range(15, 24 * 60 + 1, 15))


def build_settings_view(page: ft.Page) -> ft.Column:
    interval_val = int(get_setting("fetch_interval_minutes"))
    max_articles_val = get_setting("max_articles")
    max_bookmarks_val = get_setting("max_bookmarks")

    sources_col = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO)
    categories_col = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO)
    error_text = ft.Text("", color=ft.Colors.RED_400, size=12)
    cat_error_text = ft.Text("", color=ft.Colors.RED_400, size=12)
    new_name_field = ft.TextField(hint_text="表示名", width=140, dense=True)
    new_url_field = ft.TextField(hint_text="RSS URL (https://...)", expand=True, dense=True)
    new_cat_field = ft.TextField(hint_text="カテゴリ名", expand=True, dense=True)
    new_source_cat_dropdown = ft.Dropdown(
        hint_text="カテゴリ（任意）",
        dense=True,
        width=160,
    )

    # カテゴリ名インライン編集用state
    rename_state = {}
    # URL編集ダイアログ用state
    url_edit_state = {}
    # ソース一覧エリアの高さ
    sources_height_state = {"h": 240}

    def _rebuild_source_cat_options():
        cats = get_all_categories()
        new_source_cat_dropdown.options = [
            ft.DropdownOption(key="__none__", text="未分類"),
        ] + [ft.DropdownOption(key=str(c.id), text=c.name) for c in cats]
        if new_source_cat_dropdown.value not in {o.key for o in new_source_cat_dropdown.options}:
            new_source_cat_dropdown.value = None

    def refresh_categories():
        categories_col.controls.clear()
        _rebuild_source_cat_options()
        cats = get_all_categories()
        for cat in cats:
            cat_id = cat.id
            cat_name = cat.name

            def make_delete_cat(cid, cname):
                def on_delete(e):
                    ok, msg = delete_category(cid)
                    if ok:
                        cat_error_text.value = ""
                        refresh_categories()
                        refresh_sources()
                    else:
                        cat_error_text.value = msg
                    page.update()
                return on_delete

            def make_rename_start(cid, cname):
                def on_rename(e):
                    rename_state["id"] = cid
                    rename_state["old_name"] = cname
                    rename_field.value = cname
                    rename_dialog.open = True
                    page.update()
                return on_rename

            def make_cat_drop(target_id):
                def on_accept(e):
                    dragged_id = int(e.src.data)
                    if dragged_id != target_id:
                        swap_category_order(dragged_id, target_id)
                        refresh_categories()
                        refresh_sources()
                        page.update()
                return on_accept

            row_content = ft.Row(
                [
                    ft.Icon(ft.Icons.DRAG_HANDLE, size=16, color=ft.Colors.GREY_400),
                    ft.Text(cat_name, size=13, expand=True),
                    ft.IconButton(ft.Icons.EDIT_OUTLINED, icon_size=16, tooltip="リネーム",
                                  on_click=make_rename_start(cat_id, cat_name)),
                    ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=16, icon_color=ft.Colors.RED_300,
                                  tooltip="削除（ソースは未分類に）",
                                  on_click=make_delete_cat(cat_id, cat_name)),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=2,
            )
            drag_row = ft.DragTarget(
                group="category",
                content=ft.Draggable(
                    group="category",
                    data=str(cat_id),
                    content=ft.Container(content=row_content, padding=ft.Padding(left=2, right=2, top=2, bottom=2)),
                    content_feedback=ft.Container(
                        content=ft.Text(cat_name, size=12, color=ft.Colors.WHITE),
                        bgcolor=ft.Colors.BLUE_400,
                        border_radius=4,
                        padding=ft.Padding(left=8, right=8, top=4, bottom=4),
                    ),
                ),
                on_accept=make_cat_drop(cat_id),
            )
            categories_col.controls.append(drag_row)

    def on_add_category(e):
        name = new_cat_field.value.strip()
        if not name:
            cat_error_text.value = "カテゴリ名を入力してください"
            page.update()
            return
        ok, msg = add_category(name)
        if ok:
            new_cat_field.value = ""
            cat_error_text.value = ""
            refresh_categories()
            refresh_sources()
        else:
            cat_error_text.value = msg
        page.update()

    # リネームダイアログ
    rename_field = ft.TextField(label="新しいカテゴリ名", dense=True)
    rename_err = ft.Text("", color=ft.Colors.RED_400, size=12)

    def on_rename_ok(e):
        name = rename_field.value.strip()
        if not name:
            rename_err.value = "名前を入力してください"
            page.update()
            return
        ok, msg = rename_category(rename_state.get("id"), name)
        if ok:
            rename_dialog.open = False
            rename_err.value = ""
            refresh_categories()
            refresh_sources()
        else:
            rename_err.value = msg
        page.update()

    def on_rename_cancel(e):
        rename_dialog.open = False
        rename_err.value = ""
        page.update()

    rename_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("カテゴリをリネーム"),
        content=ft.Column([rename_field, rename_err], tight=True, spacing=4),
        actions=[
            ft.TextButton("キャンセル", on_click=on_rename_cancel),
            ft.ElevatedButton("OK", on_click=on_rename_ok),
        ],
    )
    page.overlay.append(rename_dialog)

    # URL編集ダイアログ
    url_edit_field = ft.TextField(label="RSS URL", dense=True, expand=True)
    url_edit_err = ft.Text("", color=ft.Colors.RED_400, size=12)

    def on_url_edit_ok(e):
        url = url_edit_field.value.strip()
        if not url:
            url_edit_err.value = "URLを入力してください"
            page.update()
            return
        if not url.startswith("http"):
            url_edit_err.value = "URLはhttpまたはhttpsで始めてください"
            page.update()
            return
        ok, msg = update_source_url(url_edit_state.get("id"), url)
        if ok:
            url_edit_dialog.open = False
            url_edit_err.value = ""
            refresh_sources()
        else:
            url_edit_err.value = msg
        page.update()

    def on_url_edit_cancel(e):
        url_edit_dialog.open = False
        url_edit_err.value = ""
        page.update()

    url_edit_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("URLを編集"),
        content=ft.Column([url_edit_field, url_edit_err], tight=True, spacing=4),
        actions=[
            ft.TextButton("キャンセル", on_click=on_url_edit_cancel),
            ft.ElevatedButton("OK", on_click=on_url_edit_ok),
        ],
    )
    page.overlay.append(url_edit_dialog)

    def refresh_sources():
        sources_col.controls.clear()
        cats = get_all_categories()
        cat_options = [ft.DropdownOption(key="__none__", text="未分類")] + [
            ft.DropdownOption(key=str(c.id), text=c.name) for c in cats
        ]

        for src in get_all_sources():
            src_id = src.id
            src_name = src.name
            src_builtin = src.is_builtin
            current_cat_key = str(src.category_id) if src.category_id else "__none__"

            def make_toggle(sid):
                def on_toggle(e):
                    toggle_source(sid, e.control.value)
                return on_toggle

            def make_delete(sid, sname):
                def on_delete(e):
                    ok, msg = delete_source(sid)
                    if ok:
                        refresh_sources()
                        page.update()
                    else:
                        error_text.value = msg
                        page.update()
                return on_delete

            def make_assign(sid):
                def on_assign(e):
                    v = e.control.value
                    cat_id = None if v == "__none__" else int(v)
                    assign_category(sid, cat_id)
                return on_assign

            def make_edit_url(sid, current_url):
                def on_edit_url(e):
                    url_edit_state["id"] = sid
                    url_edit_field.value = current_url or ""
                    url_edit_err.value = ""
                    url_edit_dialog.open = True
                    page.update()
                return on_edit_url

            def make_src_drop(target_id):
                def on_accept(e):
                    dragged_id = int(e.src.data)
                    if dragged_id != target_id:
                        swap_source_order(dragged_id, target_id)
                        refresh_sources()
                        page.update()
                return on_accept

            row_content = ft.Row(
                [
                    ft.Icon(ft.Icons.DRAG_HANDLE, size=16, color=ft.Colors.GREY_400),
                    ft.Switch(value=src.enabled, on_change=make_toggle(src_id), height=32),
                    ft.Text(src_name, size=13, width=110),
                    ft.Dropdown(
                        options=cat_options,
                        value=current_cat_key,
                        on_select=make_assign(src_id),
                        dense=True,
                        width=130,
                    ),
                    ft.Text(src.source_type, size=11, color=ft.Colors.GREY_500, width=100),
                    ft.Text(src.url or "-", size=11, color=ft.Colors.GREY_400, expand=True,
                            overflow=ft.TextOverflow.ELLIPSIS),
                    ft.IconButton(
                        icon=ft.Icons.EDIT_OUTLINED,
                        icon_size=16,
                        tooltip="URLを編集",
                        on_click=make_edit_url(src_id, src.url),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE,
                        icon_color=ft.Colors.RED_300 if not src_builtin else ft.Colors.GREY_300,
                        icon_size=16,
                        tooltip=None if not src_builtin else "デフォルトソースは削除不可",
                        disabled=src_builtin,
                        on_click=make_delete(src_id, src_name),
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=4,
            )
            drag_row = ft.DragTarget(
                group="source",
                content=ft.Draggable(
                    group="source",
                    data=str(src_id),
                    content=ft.Container(content=row_content, padding=ft.Padding(left=2, right=2, top=2, bottom=2)),
                    content_feedback=ft.Container(
                        content=ft.Text(src_name, size=12, color=ft.Colors.WHITE),
                        bgcolor=ft.Colors.BLUE_400,
                        border_radius=4,
                        padding=ft.Padding(left=8, right=8, top=4, bottom=4),
                    ),
                ),
                on_accept=make_src_drop(src_id),
            )
            sources_col.controls.append(drag_row)

    def on_add_source(e):
        name = new_name_field.value.strip()
        url = new_url_field.value.strip()
        if not name or not url:
            error_text.value = "表示名とURLを両方入力してください"
            page.update()
            return
        if not url.startswith("http"):
            error_text.value = "URLはhttpまたはhttpsで始めてください"
            page.update()
            return
        v = new_source_cat_dropdown.value
        category_id = None if (not v or v == "__none__") else int(v)
        ok, msg = add_rss_source(name, url, category_id)
        if ok:
            new_name_field.value = ""
            new_url_field.value = ""
            new_source_cat_dropdown.value = None
            error_text.value = ""
            refresh_sources()
        else:
            error_text.value = msg
        page.update()

    def on_interval_change(e):
        v = int(e.control.value)
        set_setting("fetch_interval_minutes", str(v))
        reschedule(v)

    def on_max_articles_change(e):
        v = e.control.value.strip()
        if v.isdigit():
            set_setting("max_articles", str(max(1, min(9999, int(v)))))

    def on_max_bookmarks_change(e):
        v = e.control.value.strip()
        if v.isdigit():
            set_setting("max_bookmarks", str(max(1, min(9999, int(v)))))

    async def go_back(e):
        await page.push_route("/")

    refresh_categories()
    refresh_sources()

    sources_area_container = ft.Container(
        content=sources_col,
        height=sources_height_state["h"],
        border=ft.Border(
            top=ft.BorderSide(1, ft.Colors.GREY_200),
            bottom=ft.BorderSide(1, ft.Colors.GREY_200),
        ),
        padding=ft.Padding(left=0, right=0, top=4, bottom=4),
    )

    def on_sources_resize(e: ft.DragUpdateEvent):
        new_h = sources_area_container.height + e.local_delta.y
        sources_area_container.height = max(80, min(new_h, 800))
        sources_height_state["h"] = sources_area_container.height
        page.update()

    sources_resize_handle = ft.GestureDetector(
        content=ft.Container(
            content=ft.Icon(ft.Icons.DRAG_HANDLE, size=16, color=ft.Colors.GREY_400),
            height=16,
            alignment=ft.Alignment(0, 0),
            bgcolor=ft.Colors.GREY_100,
            border_radius=ft.BorderRadius(bottom_left=4, bottom_right=4, top_left=0, top_right=0),
        ),
        mouse_cursor=ft.MouseCursor.RESIZE_ROW,
        on_pan_update=on_sources_resize,
    )

    return ft.Column(
        [
            ft.Row([
                ft.IconButton(ft.Icons.ARROW_BACK, on_click=go_back),
                ft.Text("設定", size=20, weight=ft.FontWeight.BOLD),
            ]),
            ft.Divider(),

            # --- カテゴリ管理 ---
            ft.Text("カテゴリ管理", weight=ft.FontWeight.W_600, size=15),
            ft.Text("↑↓で表示順変更。削除するとそのカテゴリのソースは未分類になります。", size=12, color=ft.Colors.GREY_600),
            ft.Container(content=categories_col, height=160, border=ft.Border(
                top=ft.BorderSide(1, ft.Colors.GREY_200),
                bottom=ft.BorderSide(1, ft.Colors.GREY_200),
            ), padding=ft.Padding(left=0, right=0, top=4, bottom=4)),
            ft.Row(
                [
                    new_cat_field,
                    ft.IconButton(ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.GREEN_400,
                                  tooltip="カテゴリ追加", on_click=on_add_category),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            cat_error_text,
            ft.Divider(),

            # --- ソース管理 ---
            ft.Text("情報ソース", weight=ft.FontWeight.W_600, size=15),
            ft.Text("スイッチでON/OFF。↑↓で並び替え。デフォルトソースは削除不可（無効化のみ可）。", size=12, color=ft.Colors.GREY_600),
            ft.Row(
                [
                    ft.Text("ON/OFF", width=56, size=11, color=ft.Colors.GREY_500),
                    ft.Text("名前", size=11, color=ft.Colors.GREY_500, width=110),
                    ft.Text("カテゴリ", size=11, color=ft.Colors.GREY_500, width=130),
                    ft.Text("種別", size=11, color=ft.Colors.GREY_500, width=100),
                    ft.Text("URL", size=11, color=ft.Colors.GREY_500, expand=True),
                ],
                spacing=4,
            ),
            sources_area_container,
            sources_resize_handle,
            ft.Divider(height=8),
            ft.Text("RSSソースを追加", weight=ft.FontWeight.W_600),
            ft.Row(
                [
                    new_name_field,
                    new_url_field,
                    new_source_cat_dropdown,
                    ft.IconButton(ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.BLUE_400,
                                  tooltip="追加", on_click=on_add_source),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            error_text,
            ft.Divider(),

            # --- 取得間隔 ---
            ft.Text("取得間隔", weight=ft.FontWeight.W_600),
            ft.Dropdown(
                options=[ft.DropdownOption(key=str(m), text=f"{m}分") for m in INTERVAL_OPTIONS],
                value=str(interval_val),
                on_select=on_interval_change,
                width=180,
            ),
            ft.Text("全ソース共通（15分〜24時間、15分刻み）", size=12, color=ft.Colors.GREY_600),
            ft.Divider(),

            # --- 保持件数 ---
            ft.Text("記事の最大保持件数（ソースごと）", weight=ft.FontWeight.W_600),
            ft.Row([
                ft.TextField(value=max_articles_val, on_blur=on_max_articles_change,
                             width=100, dense=True, keyboard_type=ft.KeyboardType.NUMBER),
                ft.Text("件（1〜9999）", size=13),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text("ソースごとの上限。超過時は古い記事（ブックマーク除く）から自動削除", size=12, color=ft.Colors.GREY_600),
            ft.Divider(),
            ft.Text("ブックマークの最大保持件数（カテゴリごと）", weight=ft.FontWeight.W_600),
            ft.Row([
                ft.TextField(value=max_bookmarks_val, on_blur=on_max_bookmarks_change,
                             width=100, dense=True, keyboard_type=ft.KeyboardType.NUMBER),
                ft.Text("件（1〜9999）", size=13),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text("カテゴリごとの上限。上限に達した場合の追加はブロックされます", size=12, color=ft.Colors.GREY_600),
        ],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )
