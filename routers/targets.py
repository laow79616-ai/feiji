JOIN_JOBS = {}
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
from clients.manager import ClientManager, is_dead_session_error

router = APIRouter(prefix="/targets", tags=["目标群组频道"])
JOIN_JOBS = {}

join_jobs = {}

def memory_get_skip(link):
    import sqlite3
    conn = sqlite3.connect("/opt/telegram_manager/telegram_manager.db")
    rows = conn.execute(
        "SELECT session_name, status FROM join_memory WHERE link=? AND status IN ('joined','already','bad')",
        (link,),
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}

def memory_put(session_name, link, status, msg=""):
    import sqlite3
    from datetime import datetime, timezone
    conn = sqlite3.connect("/opt/telegram_manager/telegram_manager.db")
    conn.execute(
        "INSERT OR REPLACE INTO join_memory(session_name,link,status,msg,updated_at) VALUES(?,?,?,?,?)",
        (session_name, link, status, str(msg)[:300], datetime.now(timezone.utc).isoformat()),
    )
    conn.commit(); conn.close()


import json as _json
from datetime import datetime, timezone

def _job_save(job, session_names=None, links=None, interval=None):
    import sqlite3
    conn = sqlite3.connect("/opt/telegram_manager/telegram_manager.db")
    conn.execute("""INSERT OR REPLACE INTO join_jobs_db
        (id,status,session_names,links,interval_sec,total,done,success,failed,current,details,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        job["id"], job.get("status"),
        _json.dumps(session_names or job.get("session_names") or []),
        _json.dumps(links or job.get("links") or []),
        interval if interval is not None else job.get("interval"),
        job.get("total",0), job.get("done",0), job.get("success",0), job.get("failed",0),
        job.get("current") or "",
        _json.dumps(job.get("details") or [], ensure_ascii=False),
        job.get("created_at"), datetime.now(timezone.utc).isoformat(),
    ))
    conn.commit(); conn.close()



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
    print("JOIN任务启动", job_id, "号", len(session_names), "目标", len(links))
    global JOIN_JOBS
    if "JOIN_JOBS" not in globals() or JOIN_JOBS is None:
        JOIN_JOBS = {}
    job = JOIN_JOBS.setdefault(job_id, {"status":"running","done":0,"success":0,"failed":0,"details":[],"msg":""})
    job["status"] = "running"
    job["msg"] = "后台已接手"
    try:
        await _run_join_job_inner(job_id, session_names, links, interval)
    except Exception as e:
        job["status"] = "error"
        job["msg"] = "任务异常: " + str(e)
        print("JOIN任务异常", job_id, e)
        return

async def _run_join_job_inner(job_id: str, session_names: list, links: list, interval: int):

    """一组代理上线 → 加入 → 下线 → 下一组。跳过死号/冻结/红线。"""
    import asyncio
    from collections import defaultdict
    from sqlalchemy import select
    from models import Account, Proxy
    from clients.manager import ClientManager, is_dead_session_error, clients
    from database import get_db

    global JOIN_JOBS
    if 'JOIN_JOBS' not in globals() or JOIN_JOBS is None:
        JOIN_JOBS = {}
    job = JOIN_JOBS.get(job_id)
    if not job:
        JOIN_JOBS[job_id] = {"status":"running","done":0,"success":0,"failed":0,"details":[],"msg":"任务已重建"}
        job = JOIN_JOBS[job_id]

    job["status"] = "running"

    # 取号与代理
    agen = get_db()
    db = await agen.__anext__()
    try:
        r = await db.execute(select(Account).where(Account.session_name.in_(session_names)))
        accs = list(r.scalars().all())
        r2 = await db.execute(select(Proxy))
        proxies = {p.id: p for p in r2.scalars().all()}
    finally:
        try:
            await agen.aclose()
        except Exception:
            pass

    def is_red(acc):
        hs = (getattr(acc, "health_status", None) or "").lower()
        if hs in ("dead", "frozen", "bad"):
            return True
        if getattr(acc, "frozen", False):
            return True
        return False

    groups = defaultdict(list)
    skipped = []
    for a in accs:
        if is_red(a):
            skipped.append(a.session_name)
            continue
        groups[a.proxy_id].append(a)

    job["msg"] = f"跳过红号 {len(skipped)}，共 {len(groups)} 组调度"
    job.setdefault("details", [])

    async def drop(session_name):
        if session_name in clients:
            try:
                await clients[session_name].disconnect()
            except Exception:
                pass
            clients.pop(session_name, None)

    skip_sessions=set()
    from collections import deque
    queues = {pid: deque(mem) for pid, mem in groups.items()}
    pids = list(groups.keys())
    while any(queues[pid] for pid in pids):
        if job.get("stop"):
            job["status"] = "stopped"
            job["msg"] = "已停止"
            return
        for proxy_id in pids:
            if job.get("stop"):
                job["status"] = "stopped"
                job["msg"] = "已停止"
                return
            if not queues.get(proxy_id):
                continue
            acc = queues[proxy_id].popleft()
            px = proxies.get(proxy_id)
            proxy_str = px.proxy_str if px else None
            session_name = acc.session_name
            if session_name in skip_sessions:
                continue
            job["msg"] = f"轮询 {getattr(px,'name',proxy_id)} · {session_name}"
            # 上线（带代理，禁止裸连）
            try:
                await ClientManager.reconnect(session_name, proxy_str)
            except Exception as e:
                msg = str(e)
                ok = False
                already = False
                if "two different IP" in msg:
                    msg = "session双IP已作废"
                job["failed"] = job.get("failed", 0) + 1
                job["done"] = job.get("done", 0) + 1
                job["details"].append(f"{session_name} 连接失败 {msg}")
                await drop(session_name)
                await asyncio.sleep(max(int(interval or 5), 3))
                continue

            for link in links:
                if job.get("stop"):
                    break
                job["current"] = f"{session_name} -> {link}"
                try:
                    r = await ClientManager.join_group_or_channel(session_name, link)
                    msg = (r.get("msg") if isinstance(r, dict) else str(r)) or ""
                    if is_dead_session_error(msg):
                        skip_sessions.add(session_name)
                        if isinstance(r, dict):
                            r["msg"] = "废号已跳过: " + msg[:160]
                            r["skip"] = True
                except Exception as e:
                    r = {"success": False, "msg": str(e), "already": False}
                ok = bool(r.get("success"))
                already = bool(r.get("already"))
                msg = r.get("msg") or ""
                if ok:
                    job["success"] = job.get("success", 0) + 1
                else:
                    job["failed"] = job.get("failed", 0) + 1
                job["done"] = job.get("done", 0) + 1
                job["details"].append(f"{session_name} {link} {msg}")
                try:
                    from routers.targets import save_membership
                except Exception:
                    save_membership = None
                # 写记忆（有则用现有函数）
                try:
                    agen2 = get_db()
                    db2 = await agen2.__anext__()
                    try:
                        from models import JoinMemory
                    except Exception:
                        JoinMemory = None
                    if JoinMemory:
                        st = "joined" if ok else "failed"
                        db2.add(JoinMemory(session_name=session_name, link=link, status=st, msg=msg[:300]))
                        await db2.commit()
                    await agen2.aclose()
                except Exception:
                    pass
                await asyncio.sleep(max(int(interval or 5), 3))

            await drop(session_name)

        job["msg"] = f"已下线 {getattr(px,'name',proxy_id)}"

    job["status"] = "finished"
    job["msg"] = "全部组调度完成"


@router.post("/join/start")
async def start_join_job(req: JoinJobRequest, db: AsyncSession = Depends(get_db)):
    if not req.session_names or not req.target_ids:
        raise HTTPException(400, "请选择水军号和目标")
    result = await db.execute(select(Target).where(Target.id.in_(req.target_ids)))
    targets = result.scalars().all()
    if not targets:
        raise HTTPException(400, "目标不存在")
    links = [t.link for t in targets]
    skip_map = {}
    for link in links:
        skip_map.update(memory_get_skip(link))
    names = [s for s in req.session_names if s not in skip_map]
    skipped = len(req.session_names) - len(names)
    req.session_names = names
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
    JOIN_JOBS[job_id] = {
        "id": job_id, "status": "running", "done": 0, "success": 0, "failed": 0,
        "total": max(len(req.session_names),1) * max(len(links),1),
        "details": [], "msg": "已启动调度", "current": "", "stop": False
    }
    print("JOIN写入内存", job_id, "total", JOIN_JOBS[job_id]["total"])
    asyncio.create_task(_run_join_job(job_id, req.session_names, links, req.interval))
    return {"job_id": job_id, "total": total, "skipped": skipped if "skipped" in dir() else 0, "interval": req.interval, "msg": "任务已开始"}


@router.get("/join/status/{job_id}")
async def join_job_status(job_id: str):
    # 强制读JOIN
    job = JOIN_JOBS.get(job_id) if 'JOIN_JOBS' in globals() and JOIN_JOBS is not None else None
    if job: return job
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



async def resume_join_jobs():
    import sqlite3, json, asyncio
    try:
        conn = sqlite3.connect("/opt/telegram_manager/telegram_manager.db")
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM join_jobs_db WHERE status IN ('running','pending')").fetchall()
        conn.close()
    except Exception as e:
        print("resume load error", e)
        return
    for r in rows:
        names = json.loads(r["session_names"] or "[]")
        links = json.loads(r["links"] or "[]")
        job = {
            "id": r["id"], "status": "running",
            "total": r["total"] or 0, "done": r["done"] or 0,
            "success": r["success"] or 0, "failed": r["failed"] or 0,
            "details": json.loads(r["details"] or "[]"),
            "current": "", "stop": False,
            "session_names": names, "links": links,
            "interval": r["interval_sec"] or 180,
        }
        join_jobs[r["id"]] = job
        done = r["done"] or 0
        nlink = max(len(links), 1)
        skip = min(done, len(names) * nlink)
        remain = names[skip // nlink:]
        print("恢复加入任务", r["id"], "剩余号", len(remain))
        asyncio.create_task(_run_join_job(r["id"], remain, links, r["interval_sec"] or 180))
