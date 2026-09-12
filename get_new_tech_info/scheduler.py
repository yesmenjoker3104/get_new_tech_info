import asyncio
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy import select, func, delete

from .database import get_session, get_setting, get_enabled_sources
from .fetchers.github_trending import GitHubTrendingFetcher
from .fetchers.hackernews import HackerNewsFetcher
from .fetchers.ogp import fetch_ogp_description
from .fetchers.rss import RssFetcher
from .models import Article

scheduler = BackgroundScheduler()
_job_id = "fetch_all_job"


def _build_fetchers():
    fetchers = []
    for src in get_enabled_sources():
        if src.source_type == "rss":
            fetchers.append(RssFetcher(src.url, src.name))
        elif src.source_type == "hackernews":
            fetchers.append(HackerNewsFetcher())
        elif src.source_type == "github_trending":
            fetchers.append(GitHubTrendingFetcher())
    return fetchers

# UI更新コールバック（起動後にセット）
_on_fetch_done: list = []


def register_on_fetch_done(callback):
    _on_fetch_done.clear()
    _on_fetch_done.append(callback)


def fetch_all_sync() -> int:
    """同期版fetch_all（BackgroundSchedulerから呼ばれる）"""
    loop = asyncio.new_event_loop()
    try:
        saved, _ = loop.run_until_complete(_fetch_all_async())
        return saved
    finally:
        loop.close()
        for cb in _on_fetch_done:
            try:
                cb()
            except Exception:
                pass


def fetch_all_sync_silent() -> tuple[int, list[dict]]:
    """UI更新コールバックを呼ばない版（手動リフレッシュ用）"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_fetch_all_async())
    finally:
        loop.close()


def fetch_all_sync_with_progress(on_progress=None) -> tuple[int, list[dict]]:
    """進捗コールバック付き版。
    on_progress は asyncio ループの外のディスパッチャスレッドから呼ばれるため page.update() が安全に動作する。
    """
    import queue as _queue

    if not on_progress:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_fetch_all_async(None))
        finally:
            loop.close()

    q: _queue.Queue = _queue.Queue()

    def _proxy(done, total, source_name, ok):
        q.put((done, total, source_name, ok))

    def _dispatcher():
        while True:
            item = q.get()
            if item is None:
                break
            try:
                on_progress(*item)
            except Exception:
                pass

    dispatcher = threading.Thread(target=_dispatcher, daemon=True)
    dispatcher.start()

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_fetch_all_async(_proxy))
    finally:
        loop.close()

    q.put(None)
    dispatcher.join(timeout=2)
    return result


async def _fetch_all_async(on_progress=None) -> tuple[int, list[dict]]:
    fetchers = _build_fetchers()
    total = len(fetchers)
    completed = 0
    lock = asyncio.Lock()

    async def _fetch_one(fetcher):
        nonlocal completed
        try:
            articles = await fetcher.fetch()
            ok = True
        except Exception:
            articles = []
            ok = False
        async with lock:
            completed += 1
            done = completed
        if on_progress:
            try:
                on_progress(done, total, fetcher.source_name, ok)
            except Exception:
                pass
        return fetcher.source_name, ok, articles

    fetch_results = await asyncio.gather(*[_fetch_one(f) for f in fetchers])

    saved = 0
    new_ids = []
    source_results = []

    with get_session() as session:
        for source_name, ok, articles in fetch_results:
            cnt = 0
            if ok:
                for article_data in articles:
                    stmt = insert(Article).values(
                        url=article_data.url,
                        title=article_data.title,
                        source=article_data.source,
                        published_at=article_data.published_at,
                        summary=article_data.summary,
                        fetched_at=datetime.utcnow(),
                        is_read=False,
                        is_bookmarked=False,
                        ogp_fetched=bool(article_data.summary),
                    ).on_conflict_do_nothing(index_elements=["url"])
                    result_proxy = session.execute(stmt)
                    if result_proxy.rowcount > 0:
                        saved += 1
                        cnt += 1
                        row_id = session.scalar(
                            select(Article.id).where(Article.url == article_data.url)
                        )
                        if row_id:
                            new_ids.append(row_id)
            source_results.append({"source": source_name, "ok": ok, "count": cnt})
        session.commit()
        _enforce_article_limit(session)

    if new_ids:
        threading.Thread(target=_fetch_ogp_batch_sync, args=(new_ids,), daemon=True).start()

    return saved, source_results


def _enforce_article_limit(session) -> None:
    max_per_source = int(get_setting("max_articles"))
    sources = session.scalars(select(Article.source).distinct()).all()
    ids_to_delete = []
    for source in sources:
        count = session.scalar(
            select(func.count()).select_from(Article).where(Article.source == source)
        )
        if count <= max_per_source:
            continue
        excess = count - max_per_source
        old_ids = session.scalars(
            select(Article.id)
            .where(Article.source == source, Article.is_bookmarked == False)
            .order_by(Article.fetched_at.asc())
            .limit(excess)
        ).all()
        ids_to_delete.extend(old_ids)
    if ids_to_delete:
        session.execute(delete(Article).where(Article.id.in_(ids_to_delete)))
        session.commit()


def _fetch_ogp_batch_sync(article_ids: list[int]) -> None:
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_fetch_ogp_batch_async(article_ids))
    finally:
        loop.close()
        for cb in _on_fetch_done:
            try:
                cb()
            except Exception:
                pass


async def _fetch_ogp_batch_async(article_ids: list[int]) -> None:
    with get_session() as session:
        articles = session.scalars(
            select(Article).where(
                Article.id.in_(article_ids),
                Article.ogp_fetched == False,
            )
        ).all()
        for article in articles:
            desc = await fetch_ogp_description(article.url)
            if desc:
                article.summary = desc
            article.ogp_fetched = True
        session.commit()


def start_scheduler() -> None:
    interval = int(get_setting("fetch_interval_minutes"))
    scheduler.add_job(
        fetch_all_sync, "interval", minutes=interval,
        id=_job_id, replace_existing=True,
    )
    if not scheduler.running:
        scheduler.start()


def reschedule(interval_minutes: int) -> None:
    if scheduler.get_job(_job_id):
        scheduler.reschedule_job(_job_id, trigger="interval", minutes=interval_minutes)
    else:
        scheduler.add_job(fetch_all_sync, "interval", minutes=interval_minutes, id=_job_id, replace_existing=True)


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
