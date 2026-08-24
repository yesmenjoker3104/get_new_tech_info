from datetime import datetime

import httpx

from .base import ArticleData, BaseFetcher

HN_API = "https://hacker-news.firebaseio.com/v0"
TOP_STORIES_LIMIT = 30


class HackerNewsFetcher(BaseFetcher):
    source_name = "HackerNews"

    async def fetch(self) -> list[ArticleData]:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{HN_API}/topstories.json")
            ids = resp.json()[:TOP_STORIES_LIMIT]

            articles = []
            for item_id in ids:
                try:
                    r = await client.get(f"{HN_API}/item/{item_id}.json")
                    item = r.json()
                    url = item.get("url") or f"https://news.ycombinator.com/item?id={item_id}"
                    title = item.get("title", "")
                    if not title:
                        continue
                    published_at = datetime.utcfromtimestamp(item["time"]) if item.get("time") else None
                    articles.append(ArticleData(
                        url=url,
                        title=title,
                        source=self.source_name,
                        published_at=published_at,
                    ))
                except Exception:
                    continue
        return articles
