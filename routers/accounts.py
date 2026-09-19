import asyncio
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from typing import List, Optional
import uuid

from database import get_db
IMPORT_JOBS={}

LINE_ONLINE_JOBS = {}

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
                    created_at=__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    
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
    result = await db.execute(select(Account).order_by(Account.proxy_id, Account.id))
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
            "created_at": (str(a.created_at) if getattr(a,"created_at",None) else None),
            "is_monitor": bool(getattr(a, "is_monitor", False)),
            "health_status": getattr(a, "health_status", None) or "unknown",
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
    """本条 IP 全部上线；冻结号先连。"""
    import asyncio
    from clients.manager import ClientManager

    proxy_result = await db.execute(select(Proxy).where(Proxy.name == proxy_name))
    proxy = proxy_result.scalar_one_or_none()
    if not proxy:
        return {"success": [], "failed": [f"代理不存在: {proxy_name}"]}

    result = await db.execute(select(Account).where(Account.proxy_id == proxy.id).order_by(Account.id))
    accs = list(result.scalars().all())

    def _frozen(a):
        st = str(getattr(a, "health_status", None) or getattr(a, "status", None) or "").lower()
        if getattr(a, "is_frozen", False):
            return True
        return any(k in st for k in ("冻结", "frozen", "freeze"))

    accs.sort(key=lambda a: (not _frozen(a), a.id or 0))
    sem = asyncio.Semaphore(5)

    async def one(acc):
        async with sem:
            try:
                await ClientManager.reconnect(acc.session_name, proxy.proxy_str)
                acc.is_online = True
                return ("ok", acc.phone)
            except Exception as e:
                acc.is_online = False
                return ("fail", f"{acc.phone}: {e}")

    results = await asyncio.gather(*[one(a) for a in accs], return_exceptions=True)
    success, failed = [], []
    for r in results:
        if isinstance(r, Exception):
            failed.append(str(r))
        elif r[0] == "ok":
            success.append(r[1])
        else:
            failed.append(r[1])
    await db.commit()
    return {"success": success, "failed": failed, "msg": f"本线上线 {len(success)}/{len(accs)}"}


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


@router.post("/{session_name}/set_monitor")
async def set_monitor(session_name: str, enable: bool = True, db: AsyncSession = Depends(get_db)):
    """设置/取消监听号（用于查询群人数）"""
    result = await db.execute(select(Account).where(Account.session_name == session_name))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(404, "账号不存在")
    # 先取消其他监听号（只保留一个）
    if enable:
        all_r = await db.execute(select(Account).where(Account.is_monitor == True))
        for a in all_r.scalars().all():
            a.is_monitor = False
    acc.is_monitor = enable
    await db.commit()
    # 若启用则立即连接
    if enable:
        try:
            from models import Proxy
            pr = await db.execute(select(Proxy).where(Proxy.id == acc.proxy_id))
            proxy = pr.scalar_one_or_none()
            proxy_str = proxy.proxy_str if proxy else None
            if proxy_str:
                await ClientManager.reconnect(session_name, proxy_str)
            else:
                await ClientManager.reconnect(session_name)
            acc.is_online = True
            await db.commit()
        except Exception as e:
            return {"ok": True, "is_monitor": True, "online": False, "msg": f"已标记监听，但连接失败: {e}"}
        return {"ok": True, "is_monitor": True, "online": True, "msg": "已设为监听号并上线"}
    return {"ok": True, "is_monitor": False, "msg": "已取消监听号"}


import os
import re
import json
import zipfile
import tempfile
import shutil
from fastapi import File, UploadFile, Form


class CheckHealthRequest(BaseModel):
    session_names: List[str] = None

@router.post("/check-health")
async def check_health(req: CheckHealthRequest = None, db: AsyncSession = Depends(get_db)):
    """批量检测号是否活跃。不传 session_names 则检测全部"""
    from models import Proxy
    session_names = req.session_names if req else None
    q = select(Account)
    if session_names:
        q = q.where(Account.session_name.in_(session_names))
    r = await db.execute(q)
    accounts = r.scalars().all()
    results = []
    for acc in accounts:
        item = {"phone": acc.phone, "session_name": acc.session_name, "status": "unknown", "msg": ""}
        try:
            pr = await db.execute(select(Proxy).where(Proxy.id == acc.proxy_id))
            proxy = pr.scalar_one_or_none()
            proxy_str = getattr(proxy, "proxy_str", None) if proxy else None
            if proxy_str:
                await ClientManager.reconnect(acc.session_name, proxy_str)
            else:
                await ClientManager.reconnect(acc.session_name)
            client = await ClientManager.get_client(acc.session_name)
            if not client or not client.is_connected():
                item["status"] = "dead"
                item["msg"] = "无法连接"
            else:
                me = await client.get_me()
                if me:
                    item["status"] = "active"
                    item["msg"] = f"活跃 @{getattr(me,'username',None) or me.id}"
                    acc.health_status = "active"
                    acc.is_online = True
                else:
                    item["status"] = "dead"
                    item["msg"] = "get_me 为空"
                    acc.health_status = "dead"
        except Exception as e:
            msg = str(e)
            # 常见失效关键字
            dead_keys = ["AUTH_KEY", "SESSION_REVOKED", "USER_DEACTIVATED", "deactivated", "revoked", "unauthorized", "frozen", "freeze", "FROZEN"]
            if any(k.lower() in msg.lower() for k in dead_keys):
                item["status"] = "dead"
                acc.health_status = "dead"
            else:
                item["status"] = "fail"
                item["msg"] = msg
                acc.health_status = "unknown"
            item["msg"] = msg
            acc.is_online = False
        results.append(item)
        await db.commit()
        await asyncio.sleep(1)
    active = sum(1 for x in results if x["status"]=="active")
    dead = sum(1 for x in results if x["status"]=="dead")
    return {"total": len(results), "active": active, "dead": dead, "details": results}


@router.post("/import-zip")
async def import_zip(
    file: UploadFile = File(...),
    api_id: int = Form(None),
    proxy_id: list[int] | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """ZIP导入：先检测活跃，死号不导入；自动分配到未满10的代理"""
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "请上传 zip 文件")

    from config import SESSIONS_DIR
    from models import Proxy, ApiCredential
    import os, re as _re, json, zipfile, tempfile, shutil

    os.makedirs(SESSIONS_DIR, exist_ok=True)

    apis_r = await db.execute(select(ApiCredential).order_by(ApiCredential.id))
    apis = apis_r.scalars().all()
    if not apis:
        raise HTTPException(400, "请先添加 API")

    async def proxy_slots():
        pr = await db.execute(select(Proxy).order_by(Proxy.id))
        proxies = pr.scalars().all()
        out = []
        for p in proxies:
            cr = await db.execute(select(Account).where(Account.proxy_id == p.id))
            used = len(list(cr.scalars().all()))
            if used < 15:
                out.append((p, used))
        return out

    tmpdir = tempfile.mkdtemp(prefix="tgzip_")
    success, dead, failed = [], [], []
    try:
        zip_path = os.path.join(tmpdir, "upload.zip")
        with open(zip_path, "wb") as f:
            f.write(await file.read())
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmpdir)

        sessions = {}
        for root, _, files in os.walk(tmpdir):
            for name in files:
                if name.lower().endswith(".session") and not name.endswith("-journal"):
                    base = name[:-8]
                    sessions[base] = os.path.join(root, name)

        api_idx = 0
        for base, sess_path in sessions.items():
            try:
                slots = await proxy_slots()
                if not slots:
                    failed.append(f"{base}: 所有代理已满(每组10个)")
                    break

                json_path = None
                for root, _, files in os.walk(tmpdir):
                    for name in files:
                        if name == base + ".json":
                            json_path = os.path.join(root, name)
                            break
                    if json_path:
                        break
                meta = {}
                if json_path and os.path.exists(json_path):
                    with open(json_path, "r", encoding="utf-8") as jf:
                        meta = json.load(jf)

                phone = str(meta.get("phone") or meta.get("phone_number") or base)
                digits = _re.sub(r"[^0-9]", "", str(phone))
                phone = phone if str(phone).startswith("+") else ("+" + digits)

                exists = await db.execute(select(Account).where(Account.phone == phone))
                if exists.scalar_one_or_none():
                    failed.append(f"{phone}: 已存在")
                    continue

                safe = _re.sub(r"[^\w+\-]", "_", phone)
                session_name = f"imp_{safe}"
                dest = os.path.join(SESSIONS_DIR, f"{session_name}.session")
                shutil.copy2(sess_path, dest)
                if os.path.exists(sess_path + "-journal"):
                    shutil.copy2(sess_path + "-journal", dest + "-journal")

                slots.sort(key=lambda x: x[1])
                proxy, _used = slots[0]
                api = apis[api_idx % len(apis)]
                api_idx += 1

                if api_id:
                    ar = await db.execute(select(ApiCredential).where(ApiCredential.id == int(api_id)))
                    a2 = ar.scalar_one_or_none()
                    if a2:
                        api = a2
                if proxy_id:
                    pr = await db.execute(select(Proxy).where(Proxy.id == int(proxy_id)))
                    p2 = pr.scalar_one_or_none()
                    if p2:
                        cr = await db.execute(select(Account).where(Account.proxy_id == p2.id))
                        if len(list(cr.scalars().all())) < 10:
                            proxy = p2

                status, msg = "unknown", ""
                try:
                    await ClientManager.reconnect(session_name, proxy.proxy_str)
                    client = await ClientManager.get_client(session_name)
                    if client and client.is_connected():
                        me = await client.get_me()
                        if me:
                            status, msg = "active", "活跃"
                        else:
                            status, msg = "dead", "get_me空"
                    else:
                        status, msg = "dead", "无法连接"
                    try:
                        if client:
                            await client.disconnect()
                    except Exception:
                        pass
                except Exception as e:
                    em = str(e)
                    dead_keys = ["AUTH_KEY", "SESSION_REVOKED", "USER_DEACTIVATED", "deactivated", "revoked", "unauthorized", "frozen", "freeze", "FROZEN"]
                    if any(k.lower() in em.lower() for k in dead_keys):
                        status = "dead"
                    else:
                        status = "fail"
                    msg = em

                if status == "dead" and "invalid literal" not in (msg or "").lower() and "failed 5 time" not in (msg or "").lower() and "Connection to Telegram" not in (msg or ""):
                    try:
                        os.remove(dest)
                    except Exception:
                        pass
                    dead.append(f"{phone}: {msg}")
                    await asyncio.sleep(1)
                    continue

                # 连不上代理/网络：仍然导入，标记 unknown，避免活号被丢掉
                if status != "active":
                    try:
                        os.remove(dest)
                    except Exception:
                        pass
                    failed.append(f"{phone}: 测不通不导入 {msg}")
                    await asyncio.sleep(1)
                    continue

                acc = Account(
                    phone=phone,
                    name=str(meta.get("first_name") or meta.get("username") or phone),
                    session_name=session_name,
                    api_id=api.id,
                    proxy_id=proxy.id,
                    is_active=True,
                    is_online=False,
                )
                if hasattr(acc, "health_status"):
                    acc.health_status = "active"
                db.add(acc)
                await db.commit()
                success.append(f"{phone} -> proxy {proxy.id}")
                await asyncio.sleep(1)
            except Exception as e:
                failed.append(f"{base}: {str(e)}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return {
        "success": len(success),
        "dead_count": len(dead),
        "failed_count": len(failed),
        "ok_list": success,
        "dead": dead,
        "failed": failed,
        "msg": f"导入活跃 {len(success)}，跳过死号 {len(dead)}，失败 {len(failed)}",
    }


import_jobs = {}



@router.get("/check-all/status/{job_id}")
async def check_all_status(job_id: str):
    job = check_all_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return job

async def _run_check_all(job_id, session_names):
    import asyncio, sqlite3
    from clients.manager import ClientManager, clients
    job = check_all_jobs[job_id]
    job["status"] = "running"
    conn = sqlite3.connect("/opt/telegram_manager/telegram_manager.db")
    for session_name in session_names:
        row = conn.execute(
            """SELECT a.phone, p.proxy_str FROM accounts a
               LEFT JOIN proxies p ON a.proxy_id=p.id
               WHERE a.session_name=?""",
            (session_name,)
        ).fetchone()
        proxy_str = row[1] if row else None
        status = "failed"
        msg = ""
        try:
            await ClientManager.reconnect(session_name, proxy_str)
            c = await ClientManager.get_client(session_name)
            if c:
                await c.get_me()
                try:
                    await c.get_dialogs(limit=1)
                    from telethon.tl.functions.account import UpdateStatusRequest
                    await c(UpdateStatusRequest(offline=True))
                except Exception as e2:
                    if any(k in str(e2).lower() for k in ["frozen", "freeze"]):
                        raise e2
                status = "alive"
        except Exception as e:
            msg = str(e)
            low = msg.lower()
            if any(k in low for k in ["duplicated","revoked","deactivated","session 无效","unauthorized","auth_key","frozen","freeze"]):
                status = "dead"
            else:
                status = "failed"
        finally:
            if session_name in clients:
                try:
                    await clients[session_name].disconnect()
                except Exception:
                    pass
                clients.pop(session_name, None)
        job["done"] += 1
        if status == "alive":
            job["alive"] += 1
            conn.execute("UPDATE accounts SET health_status='active', is_online=0 WHERE session_name=?", (session_name,))
        elif status == "dead":
            job["dead"] += 1
            conn.execute("UPDATE accounts SET health_status='dead', is_online=0 WHERE session_name=?", (session_name,))
        else:
            job["failed"] += 1
        conn.commit()
        await asyncio.sleep(1)
    conn.close()
    job["status"] = "finished"

@router.post("/rebalance")
async def rebalance_disabled(*args, **kwargs):
    return {"ok": True, "msg": "已禁用重排"}


@router.post("/rebalance-ip")
async def rebalance_disabled(*args, **kwargs):
    return {"ok": True, "msg": "已禁用重排"}


from pydantic import BaseModel
from typing import List, Optional

class AssignProxyReq(BaseModel):
    session_names: List[str]
    proxy_id: int

@router.get("/pool/unassigned")
async def list_unassigned(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.proxy_id.is_(None)))
    rows = result.scalars().all()
    return [{
        "id": a.id,
        "phone": a.phone,
        "name": a.name,
        "session_name": a.session_name,
        "created_at": (str(a.created_at) if getattr(a,"created_at",None) else None),
        "health_status": getattr(a, "health_status", None),
    } for a in rows]

@router.post("/pool/assign")
async def assign_proxy(req: AssignProxyReq, db: AsyncSession = Depends(get_db)):
    from models import Proxy
    pr = await db.execute(select(Proxy).where(Proxy.id == req.proxy_id))
    proxy = pr.scalar_one_or_none()
    if not proxy:
        return {"ok": False, "msg": "线路不存在"}
    used = (await db.execute(select(Account).where(Account.proxy_id == proxy.id))).scalars().all()
    remain = 10 - len(used)
    if remain <= 0:
        return {"ok": False, "msg": "该线路已满10个"}
    ok, fail = [], []
    names = req.session_names[:remain]
    extra_cnt = len(req.session_names) - len(names)
    for sn in names:
        r = await db.execute(select(Account).where(Account.session_name == sn))
        acc = r.scalar_one_or_none()
        if not acc:
            fail.append(sn + " 不存在")
            continue
        if acc.proxy_id:
            fail.append((acc.phone or sn) + " 已绑定线路，不移动")
            continue
        acc.proxy_id = proxy.id
        ok.append(acc.phone or sn)
    await db.commit()
    msg = "已分配 %d 个到 %s" % (len(ok), proxy.name)
    if extra_cnt > 0:
        msg += "；线路剩余不足，%d 个仍留在添加池" % extra_cnt
    return {"ok": True, "assigned": ok, "failed": fail, "msg": msg}

@router.post("/import-zip/start")
async def import_zip_start(
    file: UploadFile = File(...),
    proxy_id: list[int] | None = Form(None),
    api_id: int = Form(None),
):
    import uuid, os, asyncio
    os.makedirs("/tmp/tg_import", exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    zip_path = f"/tmp/tg_import/{job_id}.zip"
    with open(zip_path, "wb") as f:
        f.write(await file.read())
    IMPORT_JOBS[job_id] = {"status":"pending","success":0,"dead":0,"failed":0,"details":[],"msg":"排队中"}
    asyncio.create_task(_run_import_job(job_id, zip_path, proxy_id, api_id))
    return {"job_id": job_id}

@router.get("/import-zip/status/{job_id}")
async def import_zip_status(job_id: str):
    return IMPORT_JOBS.get(job_id, {"status":"missing","msg":"任务不存在"})

async def _run_import_job(job_id, zip_path, proxy_id, api_id):
    from routers.import_runner import run_zip_import
    from database import AsyncSessionLocal
    job = IMPORT_JOBS.setdefault(job_id, {"status":"pending","success":0,"dead":0,"failed":0,"details":[]})
    try:
        async with AsyncSessionLocal() as db:
            pids = proxy_id if isinstance(proxy_id, list) else ([proxy_id] if proxy_id else [])
            await run_zip_import(db, zip_path, job, proxy_id=(pids[0] if pids else None), api_id=api_id, proxy_ids=pids)
    except Exception as e:
        job["status"]="error"
        job["msg"]=str(e)

class BatchDeleteReq(BaseModel):
    ids: list[int]

@router.post("/batch-delete")
async def batch_delete(req: BatchDeleteReq, db: AsyncSession = Depends(get_db)):
    from clients.manager import clients
    deleted = 0
    for aid in req.ids:
        r = await db.execute(select(Account).where(Account.id == aid))
        acc = r.scalar_one_or_none()
        if not acc:
            continue
        sn = acc.session_name
        try:
            if sn in clients:
                await clients[sn].disconnect()
                del clients[sn]
        except Exception:
            pass
        await db.delete(acc)
        deleted += 1
    await db.commit()
    return {"deleted": deleted}

