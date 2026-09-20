
from typing import List
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from models import Account, Proxy

router = APIRouter(prefix="/accounts", tags=["pool"])

class AssignProxyReq(BaseModel):
    session_names: List[str]
    proxy_id: int | None = None
    proxy_ids: List[int] | None = None

@router.get("/pool/unassigned")
async def list_unassigned(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.proxy_id == (select(Proxy.id).where(Proxy.name=='添加池').scalar_subquery())))
    rows = result.scalars().all()
    return [{
        "id": a.id,
        "phone": a.phone,
        "name": a.name,
        "session_name": a.session_name,
        "created_at": getattr(a, "created_at", None),
        "health_status": getattr(a, "health_status", None),
    } for a in rows]

@router.post("/pool/assign")
async def assign_proxy(req: AssignProxyReq, db: AsyncSession = Depends(get_db)):
    pids = list(req.proxy_ids or [])
    if req.proxy_id:
        pids.append(req.proxy_id)
    pids = list(dict.fromkeys(pids))
    if not pids:
        return {"ok": False, "msg": "请选择线路"}
    if not req.session_names:
        return {"ok": False, "msg": "请选择水军号"}

    lines = []
    for pid in pids:
        pr = await db.execute(select(Proxy).where(Proxy.id == pid))
        proxy = pr.scalar_one_or_none()
        if not proxy:
            continue
        used = (await db.execute(select(Account).where(Account.proxy_id == proxy.id))).scalars().all()
        remain = max(0, 10 - len(used))
        lines.append({"proxy": proxy, "remain": remain})
    if not lines:
        return {"ok": False, "msg": "线路不存在"}

    names = list(req.session_names)
    ok, fail = [], []
    idx = 0
    for line in lines:
        proxy, remain = line["proxy"], line["remain"]
        while remain > 0 and idx < len(names):
            sn = names[idx]; idx += 1
            r = await db.execute(select(Account).where(Account.session_name == sn))
            acc = r.scalar_one_or_none()
            if not acc:
                fail.append(sn + " 不存在")
                continue
            if acc.proxy_id:
                fail.append((acc.phone or sn) + " 已绑定，不移动")
                continue
            acc.proxy_id = proxy.id
            ok.append("%s -> %s" % (acc.phone or sn, proxy.name))
            remain -= 1
        line["remain"] = remain
    leftover = names[idx:]
    await db.commit()
    msg = "已分配 %d 个；未分配 %d 个仍在添加池" % (len(ok), len(leftover))
    return {"ok": True, "assigned": ok, "failed": fail, "left": leftover, "msg": msg}
