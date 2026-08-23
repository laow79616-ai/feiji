from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional, List

from database import get_db
from models import Target
from clients.manager import ClientManager

router = APIRouter(prefix="/targets", tags=["目标群组频道"])


class TargetCreate(BaseModel):
    name: str
    link: str


class BatchJoinRequest(BaseModel):
    session_names: List[str]
    target_ids: List[int]


@router.post("/")
async def add_target(req: TargetCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Target).where(Target.link == req.link))
    if result.scalar_one_or_none():
        raise HTTPException(400, "该链接已存在")

    target = Target(name=req.name, link=req.link)
    db.add(target)
    await db.commit()
    await db.refresh(target)
    return {"id": target.id, "name": target.name, "link": target.link}


@router.get("/")
async def list_targets(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Target))
    targets = result.scalars().all()
    return [{"id": t.id, "name": t.name, "link": t.link} for t in targets]


@router.delete("/{target_id}")
async def delete_target(target_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Target).where(Target.id == target_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "不存在")
    await db.delete(target)
    await db.commit()
    return {"success": True}


@router.post("/join")
async def join_targets(req: BatchJoinRequest, db: AsyncSession = Depends(get_db)):
    if not req.session_names or not req.target_ids:
        raise HTTPException(400, "请选择水军号和目标")

    result = await db.execute(select(Target).where(Target.id.in_(req.target_ids)))
    targets = result.scalars().all()
    links = [t.link for t in targets]

    results = await ClientManager.batch_join(req.session_names, links)
    success_count = sum(1 for r in results if r["success"])
    return {
        "total": len(results),
        "success": success_count,
        "failed": len(results) - success_count,
        "details": results
    }