from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from typing import Optional

from database import get_db
from models import ApiCredential, Proxy, Account

router = APIRouter(prefix="/pools", tags=["API与代理池"])

MAX_PER_POOL = 10


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
    result = await db.execute(select(ApiCredential))
    apis = result.scalars().all()
    
    data = []
    for api in apis:
        count_result = await db.execute(
            select(func.count()).select_from(Account).where(Account.api_id == api.id)
        )
        count = count_result.scalar()
        data.append({
            "id": api.id,
            "name": api.name,
            "api_id": api.api_id,
            "group_no": getattr(p, "group_no", None), "note": getattr(p, "note", None) or "",
            "used": count,
            "remain": MAX_PER_POOL - count
        })
    return data


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
            "remain": MAX_PER_POOL - count
        })
    return data


@router.delete("/proxies/{proxy_id}")
async def delete_proxy(proxy_id: int, db: AsyncSession = Depends(get_db)):
    count_result = await db.execute(
        select(func.count()).select_from(Account).where(Account.proxy_id == proxy_id)
    )
    if count_result.scalar() > 0:
        raise HTTPException(400, "该代理下还有水军号，无法删除")
    
    result = await db.execute(select(Proxy).where(Proxy.id == proxy_id))
    proxy = result.scalar_one_or_none()
    if not proxy:
        raise HTTPException(404, "不存在")
    await db.delete(proxy)
    await db.commit()
    return {"success": True}


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
        raise HTTPException(400, f"该线路还有 {cnt} 个号，先删号或换线再删代理")
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
