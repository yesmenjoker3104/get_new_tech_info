"""Command-line interface for integrations such as assist-chat."""

import argparse
import json
import sys

from sqlalchemy import or_, select

from .database import get_enabled_sources, get_session, init_db
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="にゅーす・あつめーる CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="取得済み記事を検索")
    search.add_argument("query", nargs="?", default="", help="タイトルまたは概要の検索語")
    search.add_argument("--source", help="ソース名で絞り込み")
    search.add_argument("--unread", action="store_true", help="未読のみ")
    search.add_argument("--limit", type=int, default=10)

    subparsers.add_parser("fetch", help="有効なソースから記事を取得")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "search" and not 1 <= args.limit <= 100:
        print(json.dumps({"ok": False, "error": "limitは1から100の範囲で指定してください。"}, ensure_ascii=False))
        return 2
    try:
        init_db()
        if args.command == "fetch":
            saved = fetch_all_sync_silent()
            result = {"ok": True, "action": "fetch", "saved": saved}
        else:
            result = _search(args)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())