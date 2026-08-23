from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from typing import Optional
import uuid

from database import get_db
from models import Account, ApiCredential, Proxy
from clients.manager import ClientManager

router = APIRouter(prefix="/accounts", tags=["水军管理"])

MAX_PER_POOL = 10


class AddAccountRequest(BaseModel):
    phone: str
    name: Optional[str] = None
    api_id: int                 # 必须选择 API
    proxy_id: int               # 必须选择代理
    note: Optional[str] = None


class VerifyCodeRequest(BaseModel):
    phone: str
    code: str
    password: Optional[str] = None


@router.post("/add")
async def add_account(req: AddAccountRequest, db: AsyncSession = Depends(get_db)):
    # 检查 API 是否存在且未满
    api_result = await db.execute(select(ApiCredential).where(ApiCredential.id == req.api_id))
    api = api_result.scalar_one_or_none()
    if not api:
        raise HTTPException(400, "选择的 API 不存在")
    
    api_count = await db.execute(
        select(func.count()).select_from(Account).where(Account.api_id == req.api_id)
    )
    if api_count.scalar() >= MAX_PER_POOL:
        raise HTTPException(400, f"该 API 已绑定 {MAX_PER_POOL} 个水军号，已达上限")

    # 检查代理是否存在且未满
    proxy_result = await db.execute(select(Proxy).where(Proxy.id == req.proxy_id))
    proxy = proxy_result.scalar_one_or_none()
    if not proxy:
        raise HTTPException(400, "选择的代理不存在")
    
    proxy_count = await db.execute(
        select(func.count()).select_from(Account).where(Account.proxy_id == req.proxy_id)
    )
    if proxy_count.scalar() >= MAX_PER_POOL:
        raise HTTPException(400, f"该代理已绑定 {MAX_PER_POOL} 个水军号，已达上限")

    # 检查手机号是否重复
    result = await db.execute(select(Account).where(Account.phone == req.phone))
    if result.scalar_one_or_none():
        raise HTTPException(400, "该手机号已存在")

    session_name = f"acc_{uuid.uuid4().hex[:8]}"
    account = Account(
        phone=req.phone,
        name=req.name or req.phone,
        session_name=session_name,
        api_id=req.api_id,
        proxy_id=req.proxy_id,
        note=req.note
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)

    try:
        # 这里暂时仍用 config.py 里的 API，后面会改成动态
        await ClientManager.start_client(session_name, phone=req.phone, proxy_str=proxy.proxy_str)
    except Exception as e:
        raise HTTPException(500, f"发送验证码失败: {str(e)}")

    return {
        "id": account.id,
        "session_name": session_name,
        "msg": "验证码已发送，请完成登录"
    }


@router.post("/verify")
async def verify_code(req: VerifyCodeRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.phone == req.phone))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "账号不存在")

    # 获取代理
    proxy_result = await db.execute(select(Proxy).where(Proxy.id == account.proxy_id))
    proxy = proxy_result.scalar_one_or_none()
    proxy_str = proxy.proxy_str if proxy else None

    try:
        await ClientManager.login_with_code(
            account.session_name,
            req.phone,
            req.code,
            req.password,
            proxy_str=proxy_str
        )
        account.is_online = True
        await db.commit()
        return {"success": True, "msg": "登录成功"}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account))
    accounts = result.scalars().all()
    
    data = []
    for a in accounts:
        api_result = await db.execute(select(ApiCredential).where(ApiCredential.id == a.api_id))
        api = api_result.scalar_one_or_none()
        proxy_result = await db.execute(select(Proxy).where(Proxy.id == a.proxy_id))
        proxy = proxy_result.scalar_one_or_none()
        
        data.append({
            "id": a.id,
            "phone": a.phone,
            "name": a.name,
            "session_name": a.session_name,
            "api_name": api.name if api else "-",
            "proxy_name": proxy.name if proxy else "-",
            "is_online": a.is_online,
            "note": a.note
        })
    return data


@router.get("/{session_name}/codes")
async def get_codes(session_name: str):
    codes = await ClientManager.get_recent_codes(session_name)
    return {"codes": codes}


@router.delete("/{account_id}")
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "账号不存在")
    await db.delete(account)
    await db.commit()
    return {"success": True}
@router.post("/{session_name}/reconnect")
async def reconnect_account(session_name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.session_name == session_name))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "账号不存在")

    proxy_result = await db.execute(select(Proxy).where(Proxy.id == account.proxy_id))
    proxy = proxy_result.scalar_one_or_none()
    proxy_str = proxy.proxy_str if proxy else None

    try:
        await ClientManager.reconnect(session_name, proxy_str)
        account.is_online = True
        await db.commit()
        return {"success": True, "msg": "重新连接成功"}
    except Exception as e:
        account.is_online = False
        await db.commit()
        raise HTTPException(400, str(e))



@router.post("/line/online")
async def line_online(proxy_name: str, db: AsyncSession = Depends(get_db)):
    """一键上线某条代理下的所有账号"""
    from models import Account, Proxy
    from sqlalchemy import select
    from clients.manager import ClientManager

    proxy_result = await db.execute(select(Proxy).where(Proxy.name == proxy_name))
    proxy = proxy_result.scalar_one_or_none()
    if not proxy:
        return {"success": [], "failed": [f"代理不存在: {proxy_name}"]}

    result = await db.execute(select(Account).where(Account.proxy_id == proxy.id))
    accounts = result.scalars().all()

    success, failed = [], []
    for acc in accounts:
        try:
            await ClientManager.reconnect(acc.session_name, proxy.proxy_str)
            acc.is_online = True
            success.append(acc.phone)
        except Exception as e:
            failed.append(f"{acc.phone}: {str(e)}")

    await db.commit()
    return {"success": success, "failed": failed}


@router.post("/line/offline")
async def line_offline(proxy_name: str, db: AsyncSession = Depends(get_db)):
    """一键下线某条代理下的所有账号"""
    from models import Account, Proxy
    from sqlalchemy import select
    from clients.manager import clients

    proxy_result = await db.execute(select(Proxy).where(Proxy.name == proxy_name))
    proxy = proxy_result.scalar_one_or_none()
    if not proxy:
        return {"offline": [], "msg": f"代理不存在: {proxy_name}"}

    result = await db.execute(select(Account).where(Account.proxy_id == proxy.id))
    accounts = result.scalars().all()

    offline = []
    for acc in accounts:
        session_name = acc.session_name
        if session_name in clients:
            try:
                await clients[session_name].disconnect()
                del clients[session_name]
            except Exception:
                pass
        acc.is_online = False
        offline.append(acc.phone)

    await db.commit()
    return {"offline": offline, "msg": "已下线"}
