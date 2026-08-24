import sys
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, exc, select, text, update
from sqlalchemy.orm import Session

from .models import AppSettings, Base, FeedSource, SourceCategory, SETTING_DEFAULTS, DEFAULT_SOURCES, DEFAULT_CATEGORY

if getattr(sys, "frozen", False):
    DB_PATH = Path(sys.executable).parent / "get_new_tech_info.db"
else:
    DB_PATH = Path(__file__).parent.parent / "get_new_tech_info.db"

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


def _migrate() -> None:
    with engine.connect() as conn:
        for stmt in [
            "ALTER TABLE feed_sources ADD COLUMN category_id INTEGER REFERENCES source_categories(id)",
            "ALTER TABLE feed_sources ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0",
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except exc.OperationalError:
                pass


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate()
    with Session(engine) as session:
        for key, value in SETTING_DEFAULTS.items():
            if not session.scalar(select(AppSettings).where(AppSettings.key == key)):
                session.add(AppSettings(key=key, value=value))

        # デフォルトカテゴリ「テクノロジー」
        cat = session.scalar(select(SourceCategory).where(SourceCategory.name == DEFAULT_CATEGORY))
        if not cat:
            cat = SourceCategory(name=DEFAULT_CATEGORY, display_order=0)
            session.add(cat)
            session.flush()

        for i, src_data in enumerate(DEFAULT_SOURCES):
            existing = session.scalar(select(FeedSource).where(FeedSource.name == src_data["name"]))
            if not existing:
                session.add(FeedSource(**src_data, enabled=True, category_id=cat.id, display_order=i))
            elif existing.category_id is None:
                existing.category_id = cat.id

        # display_order が全て0のとき（新規マイグレーション）は id 順で初期化
        sources = session.scalars(select(FeedSource).order_by(FeedSource.id)).all()
        if all(s.display_order == 0 for s in sources):
            for i, s in enumerate(sources):
                s.display_order = i

        session.commit()


def get_all_sources() -> list[FeedSource]:
    with get_session() as session:
        return session.scalars(
            select(FeedSource).order_by(FeedSource.display_order, FeedSource.id)
        ).all()


def get_enabled_sources() -> list[FeedSource]:
    with get_session() as session:
        return session.scalars(
            select(FeedSource)
            .where(FeedSource.enabled == True)
            .order_by(FeedSource.display_order, FeedSource.id)
        ).all()


def add_rss_source(name: str, url: str, category_id: int | None = None) -> tuple[bool, str]:
    with get_session() as session:
        if session.scalar(select(FeedSource).where(FeedSource.name == name)):
            return False, f"「{name}」は既に登録されています"
        if session.scalar(select(FeedSource).where(FeedSource.url == url)):
            return False, "同じURLが既に登録されています"
        max_order = session.scalar(
            select(FeedSource.display_order).order_by(FeedSource.display_order.desc()).limit(1)
        ) or 0
        session.add(FeedSource(
            name=name, source_type="rss", url=url,
            enabled=True, is_builtin=False, display_order=max_order + 1,
            category_id=category_id,
        ))
        session.commit()
        return True, ""


def toggle_source(source_id: int, enabled: bool) -> None:
    with get_session() as session:
        src = session.get(FeedSource, source_id)
        if src:
            src.enabled = enabled
            session.commit()


def update_source_url(source_id: int, url: str) -> tuple[bool, str]:
    with get_session() as session:
        src = session.get(FeedSource, source_id)
        if not src:
            return False, "ソースが見つかりません"
        existing = session.scalar(
            select(FeedSource).where(FeedSource.url == url, FeedSource.id != source_id)
        )
        if existing:
            return False, "同じURLが既に登録されています"
        src.url = url
        session.commit()
        return True, ""


def delete_source(source_id: int) -> tuple[bool, str]:
    with get_session() as session:
        src = session.get(FeedSource, source_id)
        if not src:
            return False, "ソースが見つかりません"
        if src.is_builtin:
            return False, "デフォルトソースは削除できません（無効化してください）"
        session.delete(src)
        session.commit()
        return True, ""


def assign_category(source_id: int, category_id: int | None) -> None:
    with get_session() as session:
        src = session.get(FeedSource, source_id)
        if src:
            src.category_id = category_id
            session.commit()


def swap_source_order(id1: int, id2: int) -> None:
    with get_session() as session:
        s1 = session.get(FeedSource, id1)
        s2 = session.get(FeedSource, id2)
        if s1 and s2:
            s1.display_order, s2.display_order = s2.display_order, s1.display_order
            if s1.display_order == s2.display_order:
                sources = session.scalars(
                    select(FeedSource).order_by(FeedSource.display_order, FeedSource.id)
                ).all()
                for i, s in enumerate(sources):
                    s.display_order = i
            session.commit()


def reorder_source(source_id: int, direction: int) -> None:
    """direction: -1=上へ, +1=下へ"""
    with get_session() as session:
        sources = session.scalars(
            select(FeedSource).order_by(FeedSource.display_order, FeedSource.id)
        ).all()
        idx = next((i for i, s in enumerate(sources) if s.id == source_id), None)
        if idx is None:
            return
        swap_idx = idx + direction
        if swap_idx < 0 or swap_idx >= len(sources):
            return
        sources[idx].display_order, sources[swap_idx].display_order = (
            sources[swap_idx].display_order, sources[idx].display_order
        )
        if sources[idx].display_order == sources[swap_idx].display_order:
            sources[idx].display_order = swap_idx
            sources[swap_idx].display_order = idx
        session.commit()


# --- カテゴリCRUD ---

def get_all_categories() -> list[SourceCategory]:
    with get_session() as session:
        return session.scalars(
            select(SourceCategory).order_by(SourceCategory.display_order, SourceCategory.id)
        ).all()


def add_category(name: str) -> tuple[bool, str]:
    with get_session() as session:
        if session.scalar(select(SourceCategory).where(SourceCategory.name == name)):
            return False, f"「{name}」は既に存在します"
        max_order = session.scalar(
            select(SourceCategory.display_order).order_by(SourceCategory.display_order.desc()).limit(1)
        ) or 0
        session.add(SourceCategory(name=name, display_order=max_order + 1))
        session.commit()
        return True, ""


def delete_category(category_id: int) -> tuple[bool, str]:
    with get_session() as session:
        cat = session.get(SourceCategory, category_id)
        if not cat:
            return False, "カテゴリが見つかりません"
        # 使用中ソースを未分類に
        session.execute(
            update(FeedSource).where(FeedSource.category_id == category_id).values(category_id=None)
        )
        session.delete(cat)
        session.commit()
        return True, ""


def rename_category(category_id: int, name: str) -> tuple[bool, str]:
    with get_session() as session:
        if session.scalar(select(SourceCategory).where(SourceCategory.name == name)):
            return False, f"「{name}」は既に存在します"
        cat = session.get(SourceCategory, category_id)
        if not cat:
            return False, "カテゴリが見つかりません"
        cat.name = name
        session.commit()
        return True, ""


def swap_category_order(id1: int, id2: int) -> None:
    with get_session() as session:
        c1 = session.get(SourceCategory, id1)
        c2 = session.get(SourceCategory, id2)
        if c1 and c2:
            c1.display_order, c2.display_order = c2.display_order, c1.display_order
            if c1.display_order == c2.display_order:
                cats = session.scalars(
                    select(SourceCategory).order_by(SourceCategory.display_order, SourceCategory.id)
                ).all()
                for i, c in enumerate(cats):
                    c.display_order = i
            session.commit()


def reorder_category(category_id: int, direction: int) -> None:
    """direction: -1=上へ, +1=下へ"""
    with get_session() as session:
        cats = session.scalars(
            select(SourceCategory).order_by(SourceCategory.display_order, SourceCategory.id)
        ).all()
        idx = next((i for i, c in enumerate(cats) if c.id == category_id), None)
        if idx is None:
            return
        swap_idx = idx + direction
        if swap_idx < 0 or swap_idx >= len(cats):
            return
        cats[idx].display_order, cats[swap_idx].display_order = (
            cats[swap_idx].display_order, cats[idx].display_order
        )
        if cats[idx].display_order == cats[swap_idx].display_order:
            cats[idx].display_order = swap_idx
            cats[swap_idx].display_order = idx
        session.commit()


@contextmanager
def get_session():
    with Session(engine) as session:
        yield session


def get_setting(key: str) -> str:
    with get_session() as session:
        row = session.scalar(select(AppSettings).where(AppSettings.key == key))
        return row.value if row else SETTING_DEFAULTS.get(key, "")


def set_setting(key: str, value: str) -> None:
    with get_session() as session:
        row = session.scalar(select(AppSettings).where(AppSettings.key == key))
        if row:
            row.value = value
        else:
            session.add(AppSettings(key=key, value=value))
        session.commit()
