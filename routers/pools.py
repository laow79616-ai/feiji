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
    result = await db.execute(select(Proxy).where(Proxy.proxy_str == req.proxy_str))
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
            "used": count,
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