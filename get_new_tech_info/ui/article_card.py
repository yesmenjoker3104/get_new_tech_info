import colorsys
import webbrowser
from datetime import datetime, timedelta, timezone

import flet as ft

JST = timezone(timedelta(hours=9))


def _to_jst_str(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.replace(tzinfo=timezone.utc).astimezone(JST).strftime("%Y/%m/%d %H:%M")


# ──────────────────────────────────────────────
# 1024色パレット生成（黄金角ハッシュで色相を均等分散）
# 4グループ（明度・彩度を微妙にずらす）× 256ハッシュ = 1024色
# 白文字が読める明度帯（L=0.36〜0.50）に固定
# ──────────────────────────────────────────────
def _build_palette(n: int) -> list[str]:
    _GOLDEN = 0.618033988749895
    _GROUPS = [
        (0.72, 0.38),  # 高彩度・暗め
        (0.65, 0.46),  # 中彩度・中明度
        (0.78, 0.42),  # 最高彩度・中暗
        (0.60, 0.50),  # 低彩度・明るめ
    ]
    result = []
    for i in range(n):
        hue = (i * _GOLDEN) % 1.0
        s, l = _GROUPS[i % len(_GROUPS)]
        r, g, b = colorsys.hls_to_rgb(hue, l, s)
        result.append(f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}")
    return result


_PALETTE_SIZE = 1024
_PALETTE = _build_palette(_PALETTE_SIZE)

# 固定ソース（既存の色を維持）
_FIXED = {
    "Zenn":            "#4a90d9",
    "Qiita":           "#55c500",
    "HackerNews":      "#ff6600",
    "GitHub Trending": "#7c3aed",
    "DevelopersIO":    "#0891b2",
}

# ソース名 → 色 のマッピング（rebuild_articles ごとに更新）
_source_color_map: dict[str, str] = {}


def update_source_color_map(ordered_names: list[str]) -> None:
    """DB id 順のソース名リストを受け取り、重複なく色を割り当てる。
    固定ソースは _FIXED 色を使用し、パレット枠を消費しない。
    1024 ソース超過分はハッシュ割り当て（重複あり）。
    """
    _source_color_map.clear()
    palette_idx = 0
    for name in ordered_names:
        if name in _FIXED:
            _source_color_map[name] = _FIXED[name]
        elif palette_idx < _PALETTE_SIZE:
            _source_color_map[name] = _PALETTE[palette_idx]
            palette_idx += 1
        else:
            # 1024超: ハッシュで重複あり
            _source_color_map[name] = _PALETTE[sum(ord(c) for c in name) % _PALETTE_SIZE]


def _source_chip(source: str) -> ft.Container:
    color = _source_color_map.get(source) or _PALETTE[sum(ord(c) for c in source) % _PALETTE_SIZE]
    return ft.Container(
        content=ft.Text(source, size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.W_500),
        bgcolor=color,
        border_radius=4,
        padding=ft.Padding(left=6, right=6, top=2, bottom=2),
    )


def _new_chip() -> ft.Container:
    return ft.Container(
        content=ft.Text("NEW", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.W_700),
        bgcolor=ft.Colors.RED_400,
        border_radius=4,
        padding=ft.Padding(left=6, right=6, top=2, bottom=2),
    )


def article_card(
    article: dict, on_read, on_bookmark, on_delete, on_mark_read_only, on_select, is_selected
) -> tuple[ft.Card, ft.Container, ft.IconButton]:
    """戻り値: (card, inner_container, read_btn) — in-place更新用"""
    is_new = (datetime.now(timezone.utc) - article["fetched_at"].replace(tzinfo=timezone.utc)) < timedelta(hours=24)
    is_read = article["is_read"]
    is_bookmarked = article["is_bookmarked"]
    pub = _to_jst_str(article["published_at"])
    fetched = _to_jst_str(article["fetched_at"])

    def open_url(e):
        webbrowser.open_new_tab(article["url"])
        on_read(article["id"])

    badges = [_source_chip(article["source"])]
    if is_new:
        badges.append(_new_chip())

    read_btn = ft.IconButton(
        icon=ft.Icons.MARK_EMAIL_READ_OUTLINED,
        icon_color=ft.Colors.BLUE_300,
        icon_size=16,
        tooltip="既読にする",
        visible=not is_read,
        on_click=lambda e: on_mark_read_only(article["id"]),
    )

    rows = [
        ft.Row(badges, spacing=4, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.GestureDetector(
            content=ft.Text(
                article["title"],
                weight=ft.FontWeight.BOLD,
                size=13,
                color=ft.Colors.GREY_600 if is_read else ft.Colors.ON_SURFACE,
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
            on_tap=open_url,
            mouse_cursor=ft.MouseCursor.CLICK,
        ),
    ]
    if article["summary"]:
        rows.append(ft.Text(
            article["summary"],
            size=12,
            color=ft.Colors.GREY_500,
            max_lines=3,
            overflow=ft.TextOverflow.ELLIPSIS,
        ))
    rows.append(ft.Row(
        [
            ft.Text(pub, size=11, color=ft.Colors.GREY_400),
            ft.Text(f"取得:{fetched}", size=11, color=ft.Colors.GREY_400),
            ft.Container(expand=True),
            read_btn,
            ft.IconButton(
                icon=ft.Icons.BOOKMARK if is_bookmarked else ft.Icons.BOOKMARK_BORDER,
                icon_color=ft.Colors.AMBER_400 if is_bookmarked else ft.Colors.GREY_400,
                icon_size=16,
                tooltip="ブックマーク",
                on_click=lambda e: on_bookmark(article["id"]),
            ),
            ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE,
                icon_color=ft.Colors.RED_300,
                icon_size=16,
                tooltip="削除",
                on_click=lambda e: on_delete(article["id"]),
            ),
        ],
        spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    ))

    inner = ft.Container(
        content=ft.Column(rows, spacing=6, tight=True),
        padding=12,
        opacity=0.55 if is_read else 1.0,
        bgcolor=ft.Colors.BLUE_50 if is_selected else None,
        border_radius=8,
        on_click=lambda e: on_select(article["id"]),
    )
    card = ft.Card(content=inner, elevation=2)
    return card, inner, read_btn


def article_row(
    article: dict, on_read, on_bookmark, on_delete, on_mark_read_only, on_select, is_selected
) -> tuple[ft.Container, ft.Container, ft.IconButton]:
    """戻り値: (outer_container, outer_container, read_btn) — in-place更新用"""
    is_new = (datetime.now(timezone.utc) - article["fetched_at"].replace(tzinfo=timezone.utc)) < timedelta(hours=24)
    is_read = article["is_read"]
    is_bookmarked = article["is_bookmarked"]
    pub = _to_jst_str(article["published_at"])
    fetched = _to_jst_str(article["fetched_at"])

    def open_url(e):
        webbrowser.open_new_tab(article["url"])
        on_read(article["id"])

    badges = [_source_chip(article["source"])]
    if is_new:
        badges.append(_new_chip())

    col_rows = [
        ft.Row(badges, spacing=4, wrap=True),
        ft.GestureDetector(
            content=ft.Text(
                article["title"],
                weight=ft.FontWeight.BOLD,
                size=13,
                color=ft.Colors.GREY_600 if is_read else ft.Colors.ON_SURFACE,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
            on_tap=open_url,
            mouse_cursor=ft.MouseCursor.CLICK,
        ),
    ]
    if article["summary"]:
        col_rows.append(ft.Text(
            article["summary"],
            size=12,
            color=ft.Colors.GREY_500,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        ))
    col_rows.append(ft.Row(
        [
            ft.Text(pub, size=11, color=ft.Colors.GREY_400),
            ft.Text(f"取得:{fetched}", size=11, color=ft.Colors.GREY_400),
        ],
        spacing=12,
    ))

    read_btn = ft.IconButton(
        icon=ft.Icons.MARK_EMAIL_READ_OUTLINED,
        icon_color=ft.Colors.BLUE_300,
        icon_size=16,
        tooltip="既読にする",
        visible=not is_read,
        on_click=lambda e: on_mark_read_only(article["id"]),
    )

    outer = ft.Container(
        content=ft.Row(
            [
                ft.Column(col_rows, spacing=2, expand=True, tight=True),
                read_btn,
                ft.IconButton(
                    icon=ft.Icons.BOOKMARK if is_bookmarked else ft.Icons.BOOKMARK_BORDER,
                    icon_color=ft.Colors.AMBER_400 if is_bookmarked else ft.Colors.GREY_400,
                    icon_size=16,
                    on_click=lambda e: on_bookmark(article["id"]),
                ),
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    icon_color=ft.Colors.RED_300,
                    icon_size=16,
                    on_click=lambda e: on_delete(article["id"]),
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=4,
        ),
        padding=ft.Padding(left=12, right=12, top=8, bottom=8),
        border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.GREY_200)),
        opacity=0.55 if is_read else 1.0,
        bgcolor=ft.Colors.BLUE_50 if is_selected else None,
        on_click=lambda e: on_select(article["id"]),
    )
    return outer, outer, read_btn
