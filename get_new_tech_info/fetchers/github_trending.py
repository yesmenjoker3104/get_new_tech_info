import httpx
from bs4 import BeautifulSoup

from .base import ArticleData, BaseFetcher

TRENDING_URL = "https://github.com/trending"


class GitHubTrendingFetcher(BaseFetcher):
    source_name = "GitHub Trending"

    async def fetch(self) -> list[ArticleData]:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; tech-feed-aggregator/1.0)"}
        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            resp = await client.get(TRENDING_URL)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        articles = []
        for repo in soup.select("article.Box-row"):
            h2 = repo.select_one("h2 a")
            if not h2:
                continue
            path = h2.get("href", "").strip("/")
            url = f"https://github.com/{path}"
            title = path.replace("/", " / ")
            desc_el = repo.select_one("p")
            summary = desc_el.get_text(strip=True) if desc_el else None
            articles.append(ArticleData(
                url=url,
                title=title,
                source=self.source_name,
                summary=summary,
            ))
        return articles
