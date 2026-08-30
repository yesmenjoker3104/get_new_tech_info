"""Command-line interface for integrations such as assist-chat."""

import argparse
import json
import sys

from sqlalchemy import func, or_, select

from .database import (
    add_category,
    add_rss_source,
    assign_category,
    delete_category,
    delete_source,
    get_all_categories,
    get_all_sources,
    get_enabled_sources,
    get_session,
    init_db,
    rename_category,
    toggle_source,
    update_source_url,
)
from .models import Article
from .scheduler import fetch_all_sync_silent


def _article_dict(article: Article) -> dict:
    return {
        "id": article.id,
        "title": article.title,
        "url": article.url,
        "summary": article.summary or "",
        "source": article.source,
        "published_at": article.published_at.isoformat() if article.published_at else None,
        "fetched_at": article.fetched_at.isoformat() if article.fetched_at else None,
        "is_read": article.is_read,
        "is_bookmarked": article.is_bookmarked,
    }


def _search(args: argparse.Namespace) -> dict:
    enabled_names = {source.name for source in get_enabled_sources()}
    with get_session() as session:
        query = select(Article).where(Article.source.in_(enabled_names))
        if args.query:
            keyword = f"%{args.query}%"
            query = query.where(or_(Article.title.ilike(keyword), Article.summary.ilike(keyword)))
        if args.source:
            query = query.where(Article.source == args.source)
        if args.unread:
            query = query.where(Article.is_read == False)
        query = query.order_by(Article.published_at.desc(), Article.id.desc()).limit(args.limit)
        articles = [_article_dict(article) for article in session.scalars(query).all()]
    return {"ok": True, "action": "search", "count": len(articles), "articles": articles}


# --- sources ---

def _sources_list() -> dict:
    sources = get_all_sources()
    categories = {c.id: c.name for c in get_all_categories()}
    return {
        "ok": True,
        "action": "sources_list",
        "count": len(sources),
        "sources": [
            {
                "id": s.id,
                "name": s.name,
                "url": s.url or "",
                "enabled": s.enabled,
                "source_type": s.source_type,
                "is_builtin": s.is_builtin,
                "category": categories.get(s.category_id),
            }
            for s in sources
        ],
    }


def _sources_add(args: argparse.Namespace) -> dict:
    category_id = None
    if args.category:
        cat = next((c for c in get_all_categories() if c.name == args.category), None)
        if not cat:
            return {"ok": False, "error": f"「{args.category}」というカテゴリが見つかりません。"}
        category_id = cat.id
    ok, error = add_rss_source(args.name, args.url, category_id)
    if not ok:
        return {"ok": False, "error": error}
    return {"ok": True, "action": "sources_add", "name": args.name, "url": args.url}


def _sources_update(args: argparse.Namespace) -> dict:
    src = next((s for s in get_all_sources() if s.name == args.name), None)
    if not src:
        return {"ok": False, "error": f"「{args.name}」というソースが見つかりません。"}
    if args.url:
        ok, error = update_source_url(src.id, args.url)
        if not ok:
            return {"ok": False, "error": error}
    if args.enable:
        toggle_source(src.id, True)
    elif args.disable:
        toggle_source(src.id, False)
    if args.category is not None:
        if args.category == "":
            assign_category(src.id, None)
        else:
            cat = next((c for c in get_all_categories() if c.name == args.category), None)
            if not cat:
                return {"ok": False, "error": f"「{args.category}」というカテゴリが見つかりません。"}
            assign_category(src.id, cat.id)
    return {"ok": True, "action": "sources_update", "name": args.name}


def _sources_count() -> dict:
    sources = get_all_sources()
    with get_session() as session:
        rows = (
            session.execute(
                select(Article.source, func.count(Article.id).label("cnt"))
                .group_by(Article.source)
            ).all()
        )
    counts_map = {row.source: row.cnt for row in rows}
    items = [
        {"name": s.name, "enabled": s.enabled, "count": counts_map.get(s.name, 0)}
        for s in sources
    ]
    total = sum(i["count"] for i in items)
    return {"ok": True, "action": "sources_count", "total": total, "sources": items}


def _sources_delete(args: argparse.Namespace) -> dict:
    src = next((s for s in get_all_sources() if s.name == args.name), None)
    if not src:
        return {"ok": False, "error": f"「{args.name}」というソースが見つかりません。"}
    ok, error = delete_source(src.id)
    if not ok:
        return {"ok": False, "error": error}
    return {"ok": True, "action": "sources_delete", "name": args.name}


# --- categories ---

def _categories_list() -> dict:
    categories = get_all_categories()
    sources = get_all_sources()
    cat_sources: dict[int | None, list] = {}
    for s in sources:
        cat_sources.setdefault(s.category_id, []).append(
            {"id": s.id, "name": s.name, "url": s.url or "", "enabled": s.enabled}
        )
    return {
        "ok": True,
        "action": "categories_list",
        "count": len(categories),
        "categories": [
            {"id": c.id, "name": c.name, "sources": cat_sources.get(c.id, [])}
            for c in categories
        ],
        "uncategorized": cat_sources.get(None, []),
    }


def _categories_add(args: argparse.Namespace) -> dict:
    ok, error = add_category(args.name)
    if not ok:
        return {"ok": False, "error": error}
    return {"ok": True, "action": "categories_add", "name": args.name}


def _categories_rename(args: argparse.Namespace) -> dict:
    cat = next((c for c in get_all_categories() if c.name == args.old_name), None)
    if not cat:
        return {"ok": False, "error": f"「{args.old_name}」というカテゴリが見つかりません。"}
    ok, error = rename_category(cat.id, args.new_name)
    if not ok:
        return {"ok": False, "error": error}
    return {"ok": True, "action": "categories_rename", "old_name": args.old_name, "new_name": args.new_name}


def _categories_delete(args: argparse.Namespace) -> dict:
    cat = next((c for c in get_all_categories() if c.name == args.name), None)
    if not cat:
        return {"ok": False, "error": f"「{args.name}」というカテゴリが見つかりません。"}
    ok, error = delete_category(cat.id)
    if not ok:
        return {"ok": False, "error": error}
    return {"ok": True, "action": "categories_delete", "name": args.name}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="にゅーす・あつめーる CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="取得済み記事を検索")
    search.add_argument("query", nargs="?", default="", help="タイトルまたは概要の検索語")
    search.add_argument("--source", help="ソース名で絞り込み")
    search.add_argument("--unread", action="store_true", help="未読のみ")
    search.add_argument("--limit", type=int, default=10)

    subparsers.add_parser("fetch", help="有効なソースから記事を取得")

    # sources
    sources = subparsers.add_parser("sources", help="情報ソースの管理")
    sources_sub = sources.add_subparsers(dest="sources_command", required=True)

    sources_sub.add_parser("list", help="登録済みソースを一覧表示")
    sources_sub.add_parser("count", help="ソースごとの記事件数を表示")

    src_add = sources_sub.add_parser("add", help="RSSソースを追加")
    src_add.add_argument("name", help="ソース名")
    src_add.add_argument("url", help="RSS URL")
    src_add.add_argument("--category", default=None, help="カテゴリ名")

    src_update = sources_sub.add_parser("update", help="ソースを更新")
    src_update.add_argument("name", help="ソース名")
    src_update.add_argument("--url", default=None, help="新しいURL")
    src_update.add_argument("--enable", action="store_true", default=False, help="有効化")
    src_update.add_argument("--disable", action="store_true", default=False, help="無効化")
    src_update.add_argument("--category", default=None, help="カテゴリ名（空文字で未分類に）")

    src_delete = sources_sub.add_parser("delete", help="ソースを削除（組み込みソース不可）")
    src_delete.add_argument("name", help="ソース名")

    # categories
    cats = subparsers.add_parser("categories", help="カテゴリの管理")
    cats_sub = cats.add_subparsers(dest="categories_command", required=True)

    cats_sub.add_parser("list", help="カテゴリとソース一覧を表示")

    cat_add = cats_sub.add_parser("add", help="カテゴリを追加")
    cat_add.add_argument("name", help="カテゴリ名")

    cat_rename = cats_sub.add_parser("rename", help="カテゴリ名を変更")
    cat_rename.add_argument("old_name", help="現在のカテゴリ名")
    cat_rename.add_argument("new_name", help="新しいカテゴリ名")

    cat_delete = cats_sub.add_parser("delete", help="カテゴリを削除（所属ソースは未分類に）")
    cat_delete.add_argument("name", help="カテゴリ名")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "search" and not 1 <= args.limit <= 100:
        print(json.dumps({"ok": False, "error": "limitは1から100の範囲で指定してください。"}, ensure_ascii=True))
        return 2
    try:
        init_db()
        if args.command == "fetch":
            result = {"ok": True, "action": "fetch", "saved": fetch_all_sync_silent()}
        elif args.command == "search":
            result = _search(args)
        elif args.command == "sources":
            if args.sources_command == "list":
                result = _sources_list()
            elif args.sources_command == "count":
                result = _sources_count()
            elif args.sources_command == "add":
                result = _sources_add(args)
            elif args.sources_command == "update":
                result = _sources_update(args)
            elif args.sources_command == "delete":
                result = _sources_delete(args)
            else:
                result = {"ok": False, "error": "不明なサブコマンドです。"}
        elif args.command == "categories":
            if args.categories_command == "list":
                result = _categories_list()
            elif args.categories_command == "add":
                result = _categories_add(args)
            elif args.categories_command == "rename":
                result = _categories_rename(args)
            elif args.categories_command == "delete":
                result = _categories_delete(args)
            else:
                result = {"ok": False, "error": "不明なサブコマンドです。"}
        else:
            result = {"ok": False, "error": "不明なコマンドです。"}
        if not result.get("ok"):
            print(json.dumps(result, ensure_ascii=True))
            return 1
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
