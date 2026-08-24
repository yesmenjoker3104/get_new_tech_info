import httpx
from bs4 import BeautifulSoup


async def fetch_ogp_description(url: str) -> str | None:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; tech-feed-aggregator/1.0)"}
        async with httpx.AsyncClient(timeout=8, headers=headers, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        tag = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
        if tag and tag.get("content"):
            return tag["content"].strip()[:300]
    except Exception:
        pass
    return None
