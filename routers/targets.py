from typing import List
import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Target
from clients.manager import ClientManager

router = APIRouter(prefix="/targets", tags=["目标群组频道"])

join_jobs = {}


class TargetCreate(BaseModel):
    name: str
    link: str


class BatchJoinRequest(BaseModel):
    session_names: List[str]
    target_ids: List[int]
    interval: int = 180


class JoinJobRequest(BaseModel):
    session_names: List[str]
    target_ids: List[int]
    interval: int = 180


@router.post("/")
async def add_target(req: TargetCreate, db: AsyncSession = Depends(get_db)):
    t = Target(name=req.name, link=req.link)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return {"id": t.id, "name": t.name, "link": t.link}


@router.get("/")
async def list_targets(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Target).order_by(Target.id))
    rows = result.scalars().all()
    out = []
    for t in rows:
        out.append({
            "id": t.id,
            "name": t.name,
            "link": t.link,
            "member_count": getattr(t, "member_count", None),
            "last_member_count": getattr(t, "last_member_count", None),
            "member_updated_at": t.member_updated_at.isoformat() if getattr(t, "member_updated_at", None) else None,
        })
    return out


@router.delete("/{target_id}")
async def delete_target(target_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Target).where(Target.id == target_id))
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(404, "目标不存在")
    await db.delete(t)
    await db.commit()
    return {"ok": True}


@router.post("/join")
async def join_targets(req: BatchJoinRequest, db: AsyncSession = Depends(get_db)):
    if not req.session_names or not req.target_ids:
        raise HTTPException(400, "请选择水军号和目标")
    result = await db.execute(select(Target).where(Target.id.in_(req.target_ids)))
    targets = result.scalars().all()
    links = [t.link for t in targets]
    results = await ClientManager.batch_join(req.session_names, links, req.interval)
    success_count = sum(1 for r in results if r.get("success"))
    return {
        "total": len(results),
        "success": success_count,
        "failed": len(results) - success_count,
        "details": results,
    }


@router.post("/leave")
async def leave_targets(req: BatchJoinRequest, db: AsyncSession = Depends(get_db)):
    if not req.session_names or not req.target_ids:
        raise HTTPException(400, "请选择水军号和目标")
    result = await db.execute(select(Target).where(Target.id.in_(req.target_ids)))
    targets = result.scalars().all()
    links = [t.link for t in targets]
    interval = getattr(req, "interval", 5) or 5
    results = await ClientManager.batch_leave(req.session_names, links, interval)
    success_count = sum(1 for r in results if r.get("success"))
    return {
        "total": len(results),
        "success": success_count,
        "failed": len(results) - success_count,
        "details": results,
    }


async def _run_join_job(job_id: str, session_names: list, links: list, interval: int):
    import asyncio
    from datetime import datetime, timezone
    from clients.manager import ClientManager
    job = join_jobs[job_id]
    job["status"] = "running"
    job["details"] = []
    job["done"] = 0
    job["success"] = 0
    job["failed"] = 0
    job["created_at"] = datetime.now(timezone.utc).isoformat()
    try:
        for session_name in session_names:
            if job.get("stop"):
                break
            for link in links:
                if job.get("stop"):
                    break
                job["current"] = f"{session_name} -> {link}"
                try:
                    result = await ClientManager.join_group_or_channel(session_name, link)
                except Exception as e:
                    result = {"success": False, "msg": str(e)}
                msg = (result.get("msg") or "")
                already = any(k in msg for k in ["已经是成员", "已在群", "ALREADY_PARTICIPANT", "already"])
                item = {
                    "session_name": session_name,
                    "link": link,
                    "success": bool(result.get("success")),
                    "already": already,
                    "msg": msg,
                }
                job["details"].append(item)
                job["done"] += 1
                if result.get("success"):
                    job["success"] += 1
                else:
                    job["failed"] += 1
                # 已在群：快速跳过；真正新加入才间隔
                if already:
                    await asyncio.sleep(0.3)
                elif result.get("success"):
                    await asyncio.sleep(max(1, int(interval)))
                else:
                    await asyncio.sleep(2)
    finally:
        job["status"] = "stopped" if job.get("stop") else "finished"
        job["current"] = ""
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        try:
            import sqlite3, json
            conn = sqlite3.connect("/opt/telegram_manager/telegram_manager.db")
            conn.execute(
                """INSERT INTO join_records
                   (job_id, total, success, failed, interval_sec, status, targets, created_at, finished_at, detail_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id,
                    job.get("total", 0),
                    job.get("success", 0),
                    job.get("failed", 0),
                    interval,
                    job["status"],
                    ",".join(links),
                    job.get("created_at"),
                    job["finished_at"],
                    json.dumps(job.get("details", [])[-200:], ensure_ascii=False),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print("join_record save error", e)

@router.post("/join/start")
async def start_join_job(req: JoinJobRequest, db: AsyncSession = Depends(get_db)):
    if not req.session_names or not req.target_ids:
        raise HTTPException(400, "请选择水军号和目标")
    result = await db.execute(select(Target).where(Target.id.in_(req.target_ids)))
    targets = result.scalars().all()
    if not targets:
        raise HTTPException(400, "目标不存在")
    links = [t.link for t in targets]
    total = len(req.session_names) * len(links)
    job_id = uuid.uuid4().hex[:12]
    join_jobs[job_id] = {
        "id": job_id,
        "status": "pending",
        "total": total,
        "done": 0,
        "success": 0,
        "failed": 0,
        "details": [],
        "current": "",
        "interval": req.interval,
        "stop": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    asyncio.create_task(_run_join_job(job_id, req.session_names, links, req.interval))
    return {"job_id": job_id, "total": total, "interval": req.interval, "msg": "任务已开始"}


@router.get("/join/status/{job_id}")
async def join_job_status(job_id: str):
    job = join_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return job


@router.get("/join/jobs")
async def list_join_jobs():
    items = sorted(join_jobs.values(), key=lambda x: x.get("created_at", ""), reverse=True)[:20]
    return [
        {
            "id": j["id"],
            "status": j["status"],
            "total": j["total"],
            "done": j["done"],
            "success": j["success"],
            "failed": j["failed"],
            "current": j.get("current", ""),
            "interval": j.get("interval"),
            "created_at": j.get("created_at"),
        }
        for j in items
    ]


@router.post("/join/stop/{job_id}")
async def stop_join_job(job_id: str):
    job = join_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    job["stop"] = True
    return {"msg": "已请求停止", "job_id": job_id}


@router.get("/{target_id}/members")
async def target_members(target_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Target).where(Target.id == target_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "目标不存在")
    from clients.manager import clients, ClientManager
    from models import Account, Proxy
    client = None
    # 所有监听号，逐个尝试重连
    mr = await db.execute(select(Account).where(Account.is_monitor == True).order_by(Account.id))
    monitors = mr.scalars().all()
    for mon in monitors:
        try:
            cl = await ClientManager.get_client(mon.session_name)
            if cl and cl.is_connected():
                client = cl
                break
            pr = await db.execute(select(Proxy).where(Proxy.id == mon.proxy_id))
            proxy = pr.scalar_one_or_none()
            proxy_str = getattr(proxy, "proxy_str", None) if proxy else None
            if proxy_str:
                await ClientManager.reconnect(mon.session_name, proxy_str)
            else:
                await ClientManager.reconnect(mon.session_name)
            cl = await ClientManager.get_client(mon.session_name)
            if cl and cl.is_connected():
                client = cl
                break
        except Exception as e:
            print("monitor reconnect fail", mon.session_name, e)
            continue
    if not client:
        for cl in list(clients.values()):
            try:
                if cl.is_connected():
                    client = cl
                    break
            except Exception:
                continue
    if not client and not monitors:
        return {"members": None, "msg": "无可用监听号连接，请先对第一组点整组上线"}
    link = (target.link or "").replace("https://t.me/", "").replace("http://t.me/", "").replace("t.me/", "").replace("@", "").strip("/")
    if "joinchat" in link or link.startswith("+"):
        return {"members": None, "msg": "邀请链接无法查询人数"}
    last_err = None
    # 用所有监听号轮流试
    candidates = []
    if client:
        candidates.append(client)
    for mon in monitors:
        try:
            cl = await ClientManager.get_client(mon.session_name)
            if cl and cl.is_connected() and cl not in candidates:
                candidates.append(cl)
        except Exception:
            pass
    from telethon.errors import FloodWaitError
    from telethon.tl.functions.channels import GetFullChannelRequest
    for cl in candidates:
        try:
            entity = await cl.get_entity(link)
            count = getattr(entity, "participants_count", None)
            if count is None:
                f = await cl(GetFullChannelRequest(entity))
                count = f.full_chat.participants_count
            target.last_member_count = target.member_count
            target.member_count = count
            target.member_updated_at = datetime.now(timezone.utc)
            await db.commit()
            return {
                "id": target.id,
                "name": target.name,
                "link": target.link,
                "members": count,
                "last_member_count": target.last_member_count,
            }
        except FloodWaitError as e:
            last_err = f"限流需等待 {e.seconds} 秒，尝试下一个监听号"
            continue
        except Exception as e:
            last_err = str(e)
            continue
    return {"members": None, "msg": last_err or "全部监听号查询失败"}


@router.get("/join/records")
async def list_join_records(limit: int = 50):
    """历史加入记录：成功/失败数量"""
    import sqlite3
    conn = sqlite3.connect("/opt/telegram_manager/telegram_manager.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, job_id, total, success, failed, interval_sec, status, targets, created_at, finished_at FROM join_records ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

