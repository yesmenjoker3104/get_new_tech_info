import ssl
import urllib.request
from datetime import datetime

import feedparser

from .base import ArticleData, BaseFetcher

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _fetch_feed(url: str) -> feedparser.FeedParserDict:
    """SSL証明書エラー時は検証なしで再試行する。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            return feedparser.parse(r.read())
    except Exception:
        pass
    # SSL検証なしで再試行
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
            return feedparser.parse(r.read())
    except Exception:
        return feedparser.parse("")


def _parse_date(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6])
            except Exception:
                pass
    return None


class RssFetcher(BaseFetcher):
    def __init__(self, url: str, source_name: str):
        self.url = url
        self.source_name = source_name

    async def fetch(self) -> list[ArticleData]:
        feed = _fetch_feed(self.url)
        articles = []
        for entry in feed.entries:
            url = entry.get("link", "")
            title = entry.get("title", "")
            if not url or not title:
                continue
            summary = entry.get("summary") or entry.get("description") or None
            if summary:
                # HTMLタグを簡易除去
                import re
                summary = re.sub(r"<[^>]+>", "", summary).strip()[:300] or None
            articles.append(ArticleData(
                url=url,
                title=title,
                source=self.source_name,
                published_at=_parse_date(entry),
                summary=summary,
            ))
        return articles


class ZennFetcher(RssFetcher):
    source_name = "Zenn"

    def __init__(self):
        super().__init__("https://zenn.dev/feed", "Zenn")


class QiitaFetcher(RssFetcher):
    source_name = "Qiita"

    def __init__(self):
        super().__init__("https://qiita.com/popular-items/feed", "Qiita")


class ClassmethodFetcher(RssFetcher):
    source_name = "DevelopersIO"

    def __init__(self):
        super().__init__("https://dev.classmethod.jp/feed/", "DevelopersIO")
