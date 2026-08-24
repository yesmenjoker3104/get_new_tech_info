import asyncio
import threading
import flet as ft

from .database import init_db
from .scheduler import start_scheduler, stop_scheduler


def _suppress_proactor_pipe_error(loop, context):
    exc = context.get("exception")
    if isinstance(exc, ConnectionResetError):
        return
    loop.default_exception_handler(context)


def main(page: ft.Page):
    try:
        asyncio.get_event_loop().set_exception_handler(_suppress_proactor_pipe_error)
    except Exception:
        pass
    page.title = "にゅーす・あつめーる"
    page.window.width = 1280
    page.window.height = 800
    page.padding = 0
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.BLUE)

    init_db()
    start_scheduler()

    # コンテンツエリア（ルートに応じて差し替え）
    content = ft.Container(expand=True)
    _main_view_cache: dict = {}

    def show_route(route: str):
        from .ui.bookmarks_view import build_bookmarks_view
        from .ui.settings_view import build_settings_view

        if route == "/bookmarks":
            if "set_active" in _main_view_cache:
                _main_view_cache["set_active"](False)
            bm_view, bm_set_active = build_bookmarks_view(page)
            _main_view_cache["bm_set_active"] = bm_set_active
            content.content = bm_view
        elif route == "/settings":
            if "set_active" in _main_view_cache:
                _main_view_cache["set_active"](False)
            content.content = ft.Container(
                content=build_settings_view(page),
                expand=True, padding=16,
            )
        else:
            if "bm_set_active" in _main_view_cache:
                _main_view_cache["bm_set_active"](False)
            if "view" not in _main_view_cache:
                from .ui.main_view import build_main_view
                view, rebuild, set_active = build_main_view(page)
                _main_view_cache["view"] = view
                _main_view_cache["rebuild"] = rebuild
                _main_view_cache["set_active"] = set_active
            else:
                # メイン画面に戻るとき: 先に表示してからバックグラウンドで再構築
                _main_view_cache["set_active"](True)
                content.content = _main_view_cache["view"]
                page.update()
                threading.Thread(target=_main_view_cache["rebuild"], daemon=True).start()
                return
            content.content = _main_view_cache["view"]
        page.update()

    async def route_change(e):
        show_route(page.route)

    page.on_route_change = route_change

    async def on_window_event(e):
        if e.type == ft.WindowEventType.CLOSE:
            stop_scheduler()

    page.window.on_event = on_window_event

    page.add(content)
    show_route("/")


if __name__ == "__main__":
    ft.run(main, assets_dir="assets")
