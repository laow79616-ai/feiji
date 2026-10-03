from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from typing import Optional

from database import get_db
from models import ApiCredential, Proxy, Account

router = APIRouter(prefix="/pools", tags=["API与代理池"])

API_CAP = 50
MAX_PER_POOL = 10  # API 每条仍 10
PROXY_CAP = 100  # 每条代理 100 个号

MAX_PER_LINE = 100

async def _spread_accounts_off_proxy(db, proxy_id: int):
    """把某条 IP 上的号摊到其它未满线路；摊不完的 proxy_id 置空。"""
    from sqlalchemy import select, func, update
    from models import Proxy, Account

    accs = (await db.execute(select(Account).where(Account.proxy_id == proxy_id))).scalars().all()
    if not accs:
        return {"moved": [], "unassigned": [], "left": 0}

    others = (await db.execute(select(Proxy).where(Proxy.id != proxy_id).order_by(Proxy.id))).scalars().all()
    slots = []
    for px in others:
        used = (await db.execute(
            select(func.count()).select_from(Account).where(Account.proxy_id == px.id)
        )).scalar() or 0
        remain = MAX_PER_LINE - int(used)
        if remain > 0:
            slots.append([px, remain])

    moved, unassigned = [], []
    si = 0
    for acc in accs:
        placed = False
        while si < len(slots):
            px, remain = slots[si]
            if remain <= 0:
                si += 1
                continue
            acc.proxy_id = px.id
            slots[si][1] = remain - 1
            moved.append(f"{acc.phone or acc.session_name}->{px.name}")
            placed = True
            break
        if not placed:
            acc.proxy_id = None
            unassigned.append(acc.phone or acc.session_name)
    return {"moved": moved, "unassigned": unassigned, "left": len(unassigned)}





# ========== API 池 ==========
class ApiCreate(BaseModel):
    name: str
    api_id: int
    api_hash: str


@router.post("/apis")
async def add_api(req: ApiCreate, db: AsyncSession = Depends(get_db)):
    api = ApiCredential(name=req.name, api_id=req.api_id, api_hash=req.api_hash)
    db.add(api)
    await db.commit()
    await db.refresh(api)
    return {"id": api.id, "name": api.name, "api_id": api.api_id}



@router.get("/apis")
async def list_apis(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func, select
    from models import ApiCredential, Account
    apis = (await db.execute(select(ApiCredential).order_by(ApiCredential.id))).scalars().all()
    used_map = {}
    try:
        rows = (await db.execute(
            select(Account.api_id, func.count(Account.id)).group_by(Account.api_id)
        )).all()
        used_map = {aid: cnt for aid, cnt in rows if aid is not None}
    except Exception:
        used_map = {}
    out = []
    for a in apis:
        out.append({
            "id": a.id,
            "name": a.name or f"API-{a.api_id}",
            "api_id": a.api_id,
            "api_hash": (a.api_hash[:8] + "...") if getattr(a, "api_hash", None) else "",
            "used": int(used_map.get(a.id, 0)),
            "cap": API_CAP,
            "remain": API_CAP - int(used_map.get(a.id, 0)),
        })
    return out


@router.delete("/apis/{api_id}")
async def delete_api(api_id: int, db: AsyncSession = Depends(get_db)):
    count_result = await db.execute(
        select(func.count()).select_from(Account).where(Account.api_id == api_id)
    )
    if count_result.scalar() > 0:
        raise HTTPException(400, "该API下还有水军号，无法删除")
    
    result = await db.execute(select(ApiCredential).where(ApiCredential.id == api_id))
    api = result.scalar_one_or_none()
    if not api:
        raise HTTPException(404, "不存在")
    await db.delete(api)
    await db.commit()
    return {"success": True}


class ApiBatchCreate(BaseModel):
    text: str


@router.post("/apis/batch")
async def batch_add_apis(req: ApiBatchCreate, db: AsyncSession = Depends(get_db)):
    lines = [line.strip() for line in req.text.strip().split('\n') if line.strip()]
    success = 0
    failed = []
    
    for i, line in enumerate(lines):
        try:
            if '|' in line:
                # 格式：备注名|api_id|api_hash
                parts = [p.strip() for p in line.split('|')]
                if len(parts) != 3:
                    failed.append(f"{line} 格式错误")
                    continue
                name, api_id_str, api_hash = parts
            elif '-' in line:
                # 格式：api_id-api_hash
                parts = line.split('-', 1)
                if len(parts) != 2:
                    failed.append(f"{line} 格式错误")
                    continue
                api_id_str, api_hash = parts[0].strip(), parts[1].strip()
                name = f"API-{api_id_str}"
            else:
                failed.append(f"{line} 格式不支持")
                continue
            
            api_id = int(api_id_str)
            exist = (await db.execute(select(ApiCredential).where(ApiCredential.api_id==api_id))).scalar_one_or_none()
            if exist:
                failed.append(f"{line} 已存在，跳过")
                continue
            api = ApiCredential(name=name, api_id=api_id, api_hash=api_hash)
            db.add(api)
            success += 1
        except Exception as e:
            failed.append(f"{line} 失败: {str(e)}")
    
    await db.commit()
    return {
        "success": success,
        "failed_count": len(failed),
        "failed": failed
    }

# ========== 代理池 ==========
class ProxyCreate(BaseModel):
    name: str
    proxy_str: str


@router.post("/proxies")
async def add_proxy(req: ProxyCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Proxy).order_by(Proxy.id).where(Proxy.proxy_str == req.proxy_str))
    if result.scalar_one_or_none():
        raise HTTPException(400, "该代理已存在")
    
    proxy = Proxy(name=req.name, proxy_str=req.proxy_str)
    db.add(proxy)
    await db.commit()
    await db.refresh(proxy)
    return {"id": proxy.id, "name": proxy.name}


@router.get("/proxies")
async def list_proxies(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Proxy))
    proxies = result.scalars().all()
    
    data = []
    for p in proxies:
        count_result = await db.execute(
            select(func.count()).select_from(Account).where(Account.proxy_id == p.id)
        )
        count = count_result.scalar()
        data.append({
            "id": p.id,
            "name": p.name,
            "proxy_str": p.proxy_str,
            "group_no": getattr(p, "group_no", None),
            "used": count,
            "is_ok": bool(getattr(p,"is_ok",1)),
            "cap": PROXY_CAP, "remain": PROXY_CAP - count
        })
    return data


@router.delete("/proxies/{proxy_id}")
async def delete_proxy(proxy_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Proxy).where(Proxy.id == proxy_id))
    proxy = result.scalar_one_or_none()
    if not proxy:
        raise HTTPException(404, "不存在")
    spread = await _spread_accounts_off_proxy(db, proxy_id)
    await db.delete(proxy)
    await db.commit()
    return {
        "success": True,
        "ok": True,
        "moved": spread.get("moved", []),
        "unassigned": spread.get("unassigned", []),
        "msg": f"已删线路，转走 {len(spread.get('moved', []))} 个，未分配 {len(spread.get('unassigned', []))} 个",
    }


class ProxyBatchCreate(BaseModel):
    text: str


@router.post("/proxies/batch")
async def batch_add_proxies(req: ProxyBatchCreate, db: AsyncSession = Depends(get_db)):
    lines = [line.strip() for line in req.text.strip().split('\n') if line.strip()]
    success = 0
    failed = []

    for line in lines:
        try:
            if '|' in line:
                name, proxy_str = line.split('|', 1)
                name = name.strip()
                proxy_str = proxy_str.strip()
            else:
                proxy_str = line.strip()
                name = f"代理-{proxy_str.split(':')[0]}"

            exists = await db.execute(select(Proxy).where(Proxy.proxy_str == proxy_str))
            if exists.scalar_one_or_none():
                failed.append(f"{proxy_str} 已存在")
                continue

            proxy = Proxy(name=name, proxy_str=proxy_str)
            db.add(proxy)
            success += 1
        except Exception as e:
            failed.append(f"{line} 失败: {str(e)}")

    await db.commit()
    return {
        "success": success,
        "failed_count": len(failed),
        "failed": failed
    }

class ProxyIds(BaseModel):
    ids: list[int]

@router.delete("/proxies/{proxy_id}")
async def delete_proxy(proxy_id: int, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select, func
    from models import Proxy, Account
    r = await db.execute(select(Proxy).where(Proxy.id == proxy_id))
    p = r.scalar_one_or_none()
    if not p:
        raise HTTPException(404, "代理不存在")
    cnt = (await db.execute(select(func.count()).select_from(Account).where(Account.proxy_id == proxy_id))).scalar() or 0
    if cnt:
        pass  # 改为摊号后删除
    await db.delete(p)
    await db.commit()
    return {"ok": True, "id": proxy_id}

@router.post("/proxies/delete-batch")
async def delete_proxies_batch(req: ProxyIds, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select, func
    from models import Proxy, Account
    ok, skip = [], []
    for pid in req.ids or []:
        r = await db.execute(select(Proxy).where(Proxy.id == pid))
        p = r.scalar_one_or_none()
        if not p:
            skip.append(f"{pid}:不存在")
            continue
        cnt = (await db.execute(select(func.count()).select_from(Account).where(Account.proxy_id == pid))).scalar() or 0
        if cnt:
            skip.append(f"{p.name}:还有{cnt}个号")
            continue
        await db.delete(p)
        ok.append(p.name)
    await db.commit()
    return {"deleted": ok, "skipped": skip, "msg": f"已删{len(ok)} 跳过{len(skip)}"}

@router.post("/proxies/clear-all")
async def clear_all_proxies(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select, update
    from models import Proxy, Account
    try:
        await db.execute(update(Account).values(proxy_id=None))
    except Exception:
        await db.execute(update(Account).values(proxy_id=0))
    rows = (await db.execute(select(Proxy))).scalars().all()
    n = 0
    for p in rows:
        await db.delete(p)
        n += 1
    await db.commit()
    return {"ok": True, "deleted": n, "msg": f"已清空 {n} 条IP，水军号保留"}

class ApiIds(BaseModel):
    ids: list[int] = []

@router.delete("/apis/{api_id}")
async def delete_api(api_id: int, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select, update
    from models import ApiCredential, Account
    r = await db.execute(select(ApiCredential).where(ApiCredential.id == api_id))
    a = r.scalar_one_or_none()
    if not a:
        raise HTTPException(404, "API不存在")
    try:
        await db.execute(update(Account).where(Account.api_id == api_id).values(api_id=None))
    except Exception:
        await db.execute(update(Account).where(Account.api_id == api_id).values(api_id=0))
    await db.delete(a)
    await db.commit()
    return {"ok": True, "id": api_id, "msg": "已删API，水军号保留"}

@router.post("/apis/delete-batch")
async def delete_apis_batch(req: ApiIds, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select, update
    from models import ApiCredential, Account
    ids = req.ids or []
    ok = []
    for aid in ids:
        r = await db.execute(select(ApiCredential).where(ApiCredential.id == aid))
        a = r.scalar_one_or_none()
        if not a:
            continue
        try:
            await db.execute(update(Account).where(Account.api_id == aid).values(api_id=None))
        except Exception:
            await db.execute(update(Account).where(Account.api_id == aid).values(api_id=0))
        await db.delete(a)
        ok.append(a.name if hasattr(a, "name") else str(aid))
    await db.commit()
    return {"deleted": ok, "count": len(ok), "msg": f"已删 {len(ok)} 条API，水军号仍在库"}

@router.post("/apis/clear-all")
async def clear_all_apis(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select, update
    from models import ApiCredential, Account
    try:
        await db.execute(update(Account).values(api_id=None))
    except Exception:
        await db.execute(update(Account).values(api_id=0))
    rows = (await db.execute(select(ApiCredential))).scalars().all()
    n = 0
    for a in rows:
        await db.delete(a)
        n += 1
    await db.commit()
    return {"ok": True, "deleted": n, "msg": f"已清空 {n} 条API，水军号保留"}


class _ProxyNoteReq(BaseModel):
    note: str = ""

@router.post("/proxies/{proxy_id}/note")
async def set_proxy_note(proxy_id: int, req: _ProxyNoteReq, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import text
    await db.execute(text("UPDATE proxies SET note=:n WHERE id=:i"), {"n": (req.note or "").strip(), "i": proxy_id})
    await db.commit()
    return {"ok": True, "id": proxy_id, "note": (req.note or "").strip()}



class _NoteByName(BaseModel):
    name: str
    note: str = ""

@router.post("/proxies/note-by-name")
async def note_by_name(req: _NoteByName, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import text
    await db.execute(
        text("UPDATE proxies SET note=:n WHERE name=:name"),
        {"n": (req.note or "").strip(), "name": req.name}
    )
    await db.commit()
    return {"ok": True, "name": req.name, "note": (req.note or "").strip()}


@router.get("/proxy-notes")
async def get_proxy_notes():
    import sqlite3
    con = sqlite3.connect("/opt/telegram_manager/telegram_manager.db")
    rows = con.execute("SELECT name, IFNULL(note,'') FROM proxies").fetchall()
    con.close()
    return {n: (note or "") for n, note in rows}


@router.delete("/proxies/{proxy_id}")
async def delete_proxy_spread(proxy_id: int, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from models import Proxy
    r = await db.execute(select(Proxy).where(Proxy.id == proxy_id))
    p = r.scalar_one_or_none()
    if not p:
        raise HTTPException(404, "代理不存在")
    spread = await _spread_accounts_off_proxy(db, proxy_id)
    await db.delete(p)
    await db.commit()
    return {
        "ok": True,
        "id": proxy_id,
        "name": p.name,
        "moved": spread["moved"],
        "unassigned": spread["unassigned"],
        "msg": f"已删线路，转走 {len(spread['moved'])} 个，未分配 {len(spread['unassigned'])} 个",
    }

@router.post("/proxies/delete-batch")
async def delete_proxies_batch_spread(req: ProxyIds, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from models import Proxy
    ok, detail = [], []
    for pid in req.ids or []:
        r = await db.execute(select(Proxy).where(Proxy.id == pid))
        p = r.scalar_one_or_none()
        if not p:
            detail.append(f"{pid}:不存在")
            continue
        spread = await _spread_accounts_off_proxy(db, pid)
        await db.delete(p)
        ok.append(p.name)
        detail.append(f"{p.name}:转走{len(spread['moved'])} 未分配{len(spread['unassigned'])}")
    await db.commit()
    return {"deleted": ok, "detail": detail, "msg": f"已删{len(ok)}条，号已摊到其它未满线路"}

