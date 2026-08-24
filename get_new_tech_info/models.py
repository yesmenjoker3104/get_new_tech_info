from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Article(Base):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(String, nullable=False, unique=True)
    title = Column(String, nullable=False)
    summary = Column(Text, nullable=True)
    source = Column(String, nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    published_at = Column(DateTime, nullable=True)
    is_read = Column(Boolean, nullable=False, default=False)
    is_bookmarked = Column(Boolean, nullable=False, default=False)
    ogp_fetched = Column(Boolean, nullable=False, default=False)


class SourceCategory(Base):
    __tablename__ = "source_categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    display_order = Column(Integer, nullable=False, default=0)


class FeedSource(Base):
    __tablename__ = "feed_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    source_type = Column(String, nullable=False)
    url = Column(String, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    is_builtin = Column(Boolean, nullable=False, default=False)
    category_id = Column(Integer, ForeignKey("source_categories.id"), nullable=True)
    display_order = Column(Integer, nullable=False, default=0)


DEFAULT_CATEGORY = "テクノロジー"

DEFAULT_SOURCES = [
    {"name": "Zenn",           "source_type": "rss",             "url": "https://zenn.dev/feed",                  "is_builtin": True},
    {"name": "Qiita",          "source_type": "rss",             "url": "https://qiita.com/popular-items/feed",   "is_builtin": True},
    {"name": "DevelopersIO",   "source_type": "rss",             "url": "https://dev.classmethod.jp/feed/",       "is_builtin": True},
    {"name": "HackerNews",     "source_type": "hackernews",      "url": None,                                     "is_builtin": True},
    {"name": "GitHub Trending","source_type": "github_trending",  "url": None,                                     "is_builtin": True},
]


class AppSettings(Base):
    __tablename__ = "app_settings"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)


SETTING_DEFAULTS = {
    "fetch_interval_minutes": "30",
    "max_articles": "100",
    "max_bookmarks": "100",
}
