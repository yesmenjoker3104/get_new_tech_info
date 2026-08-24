from abc import ABC, abstractmethod
from datetime import datetime


class ArticleData:
    def __init__(
        self,
        url: str,
        title: str,
        source: str,
        published_at: datetime | None = None,
        summary: str | None = None,
    ):
        self.url = url
        self.title = title
        self.source = source
        self.published_at = published_at
        self.summary = summary


class BaseFetcher(ABC):
    source_name: str = ""

    @abstractmethod
    async def fetch(self) -> list[ArticleData]:
        """ソースから記事一覧を取得して返す"""
        ...
