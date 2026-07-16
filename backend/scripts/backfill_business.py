"""backfill_business.py — 回填 7 张表的 business_id（v0.7+ 业务模型重构）

业务模型重构第 1 期的回填步骤（独立脚本，不放 alembic）：
- 原因：migration 必须可重放；回填是单次运营动作
- 前置门：015 migration 已跑（businesses 表 + 7 张表 business_id nullable 列已建）
- 后置门：跑通 --verify 后才能跑 017 migration（升 NOT NULL + FK）

回填优先级：
1) 创建 legacy-default 业务（slug='legacy-default', active），不存在则创建
2) notes:
     account_id → accounts.business_id
     空 → goal_id → goals.business_id
     还空 → run_id → agent_runs.business_id
     还空 → legacy-default
3) agent_runs:
     goal_id → goals.business_id
     空 → legacy-default
4) goals / accounts / devices / personas / kb_documents:
     全部 legacy-default（无更高优先级链接）
5) 报告：每张表多少行被分配、legacy-default 各占多少

用法：
    python -m scripts.backfill_business --dry-run   # 只看分配计划
    python -m scripts.backfill_business             # 真跑
    python -m scripts.backfill_business --verify    # 前置门：7 表 business_id IS NULL 全 0
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Optional

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# 允许从 /app/backend 直接执行（python scripts/backfill_business.py）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matrix.db.models import (  # noqa: E402
    Account,
    AgentRun,
    Business,
    Device,
    Goal,
    KbDocument,
    Note,
    Persona,
)


LEGACY_SLUG = "legacy-default"
LEGACY_NAME = "历史数据"

TABLES = [
    "devices",
    "accounts",
    "personas",
    "goals",
    "notes",
    "kb_documents",
    "agent_runs",
]


def get_engine_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL env var not set")
    return url


async def ensure_legacy_business(session) -> Business:
    """创建（或获取）legacy-default 业务，返回 Business 对象。"""
    existing = (
        await session.execute(
            select(Business).where(Business.slug == LEGACY_SLUG)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    biz = Business(slug=LEGACY_SLUG, name=LEGACY_NAME, status="active")
    session.add(biz)
    await session.flush()
    return biz


async def backfill_notes(
    session, legacy_business_id, *, dry_run: bool
) -> dict:
    """notes 回填：account → goal → run → legacy。"""
    if dry_run:
        # 只统计 NULL 数量，不 UPDATE
        null_count = (
            await session.execute(
                text("SELECT count(*) FROM notes WHERE business_id IS NULL")
            )
        ).scalar()
        return {"table": "notes", "would_assign": null_count or 0, "strategy": "account→goal→run→legacy"}

    # 链路 1：notes.account_id → accounts.business_id
    r1 = await session.execute(
        update(Note)
        .where(Note.business_id.is_(None), Note.account_id.is_not(None))
        .values(
            business_id=select(Account.business_id)
            .where(Account.id == Note.account_id)
            .scalar_subquery()
        )
    )
    # 链路 2：notes.goal_id → goals.business_id
    r2 = await session.execute(
        update(Note)
        .where(Note.business_id.is_(None), Note.goal_id.is_not(None))
        .values(
            business_id=select(Goal.business_id)
            .where(Goal.id == Note.goal_id)
            .scalar_subquery()
        )
    )
    # 链路 3：notes.run_id → agent_runs.business_id
    r3 = await session.execute(
        update(Note)
        .where(Note.business_id.is_(None), Note.run_id.is_not(None))
        .values(
            business_id=select(AgentRun.business_id)
            .where(AgentRun.id == Note.run_id)
            .scalar_subquery()
        )
    )
    # 链路 4：剩余全 NULL → legacy-default
    r4 = await session.execute(
        update(Note)
        .where(Note.business_id.is_(None))
        .values(business_id=legacy_business_id)
    )
    return {
        "table": "notes",
        "via_account": r1.rowcount or 0,
        "via_goal": r2.rowcount or 0,
        "via_run": r3.rowcount or 0,
        "via_legacy": r4.rowcount or 0,
    }


async def backfill_agent_runs(
    session, legacy_business_id, *, dry_run: bool
) -> dict:
    if dry_run:
        null_count = (
            await session.execute(
                text(
                    "SELECT count(*) FROM agent_runs WHERE business_id IS NULL"
                )
            )
        ).scalar()
        return {"table": "agent_runs", "would_assign": null_count or 0, "strategy": "goal→legacy"}

    r1 = await session.execute(
        update(AgentRun)
        .where(AgentRun.business_id.is_(None), AgentRun.goal_id.is_not(None))
        .values(
            business_id=select(Goal.business_id)
            .where(Goal.id == AgentRun.goal_id)
            .scalar_subquery()
        )
    )
    r2 = await session.execute(
        update(AgentRun)
        .where(AgentRun.business_id.is_(None))
        .values(business_id=legacy_business_id)
    )
    return {
        "table": "agent_runs",
        "via_goal": r1.rowcount or 0,
        "via_legacy": r2.rowcount or 0,
    }


async def backfill_table_legacy(
    session, table: str, orm_class, legacy_business_id, *, dry_run: bool
) -> dict:
    """无更高优先级链接的表：goals / accounts / devices / personas / kb_documents 全 legacy。"""
    if dry_run:
        null_count = (
            await session.execute(
                text(f"SELECT count(*) FROM {table} WHERE business_id IS NULL")
            )
        ).scalar()
        return {"table": table, "would_assign": null_count or 0, "strategy": "all→legacy"}

    result = await session.execute(
        update(orm_class)
        .where(orm_class.business_id.is_(None))
        .values(business_id=legacy_business_id)
    )
    return {"table": table, "via_legacy": result.rowcount or 0}


async def verify(session) -> int:
    """verify 模式：7 张表 business_id IS NULL 必须全 0。返回总 NULL 数（应该 = 0）。"""
    total = 0
    for table in TABLES:
        n = (
            await session.execute(
                text(f"SELECT count(*) FROM {table} WHERE business_id IS NULL")
            )
        ).scalar() or 0
        print(f"  {table}: {n} NULL")
        total += n
    return total


async def cmd_dry_run(session, legacy_business_id):
    """dry-run 模式：只显示分配计划。"""
    print("=== 回填计划（dry-run，不实际 UPDATE）===")
    notes_plan = await backfill_notes(session, legacy_business_id, dry_run=True)
    print(f"  notes: {notes_plan}")
    runs_plan = await backfill_agent_runs(session, legacy_business_id, dry_run=True)
    print(f"  agent_runs: {runs_plan}")
    for table, cls in [
        ("goals", Goal),
        ("accounts", Account),
        ("devices", Device),
        ("personas", Persona),
        ("kb_documents", KbDocument),
    ]:
        plan = await backfill_table_legacy(session, table, cls, legacy_business_id, dry_run=True)
        print(f"  {table}: {plan}")
    print()
    print("跑 `python -m scripts.backfill_business` 真跑。")


async def cmd_real_run(session, legacy_business_id):
    """真跑：实际 UPDATE。"""
    print("=== 真跑开始 ===")
    notes_result = await backfill_notes(session, legacy_business_id, dry_run=False)
    print(f"  notes: {notes_result}")
    runs_result = await backfill_agent_runs(session, legacy_business_id, dry_run=False)
    print(f"  agent_runs: {runs_result}")
    for table, cls in [
        ("goals", Goal),
        ("accounts", Account),
        ("devices", Device),
        ("personas", Persona),
        ("kb_documents", KbDocument),
    ]:
        r = await backfill_table_legacy(session, table, cls, legacy_business_id, dry_run=False)
        print(f"  {table}: {r}")
    await session.commit()
    print()
    print("=== 真跑完成。请跑 --verify 确认 7 表全 0 NULL ===")


async def cmd_verify(session):
    """verify 模式：7 张表 business_id IS NULL 必须全 0。"""
    print("=== verify（017 migration 前置门）===")
    total = await verify(session)
    if total == 0:
        print(f"PASS: 7 张表 business_id NULL 总数 = 0，可以跑 017 migration。")
        return 0
    else:
        print(f"FAIL: 还有 {total} 行 business_id IS NULL，先排查再跑 017。")
        return 1


async def main():
    parser = argparse.ArgumentParser(description="回填 business_id 到 7 张核心表")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="只显示分配计划")
    group.add_argument("--verify", action="store_true", help="验证 7 表 business_id NULL = 0")
    args = parser.parse_args()

    engine = create_async_engine(get_engine_url())
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as session:
        # 真跑 / dry-run 都要确保 legacy-default 业务存在
        if not args.verify:
            legacy = await ensure_legacy_business(session)
            await session.commit()
            print(f"legacy business: {legacy.id} (slug={legacy.slug})")
            print()

        if args.verify:
            exit_code = await cmd_verify(session)
        elif args.dry_run:
            await cmd_dry_run(session, legacy.id)
            exit_code = 0
        else:
            await cmd_real_run(session, legacy.id)
            exit_code = 0

    await engine.dispose()
    sys.exit(exit_code)


if __name__ == "__main__":
    asyncio.run(main())