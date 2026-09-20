import os, json, shutil, zipfile, asyncio
from datetime import datetime
from sqlalchemy import select, func
from models import Account, ApiCredential, Proxy

SESSIONS = "/opt/telegram_manager/sessions"

MAX_PER_LINE = 15

async def _load_line_counts(db):
    r = await db.execute(select(Proxy))
    proxies = [p for p in r.scalars().all() if p.name != "添加池"]
    counts = {}
    for p in proxies:
        n = (await db.execute(select(func.count()).select_from(Account).where(Account.proxy_id==p.id))).scalar() or 0
        counts[p.id] = n
    return proxies, counts

def _pick_line(counts):
    avail = [(pid, n) for pid, n in counts.items() if n < MAX_PER_LINE]
    if not avail:
        return None
    avail.sort(key=lambda x: x[1])
    return avail[0][0]


async def _ensure_pool(db):
    r = await db.execute(select(Proxy).where(Proxy.name == "添加池"))
    p = r.scalar_one_or_none()
    if p:
        return p
    p = Proxy(name="添加池", proxy_str="POOL:0:none:none")
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p

async def _pick_api(db):
    r = await db.execute(select(ApiCredential).order_by(ApiCredential.id))
    apis = r.scalars().all()
    if not apis:
        return None
    best, best_n = None, 10**9
    for a in apis:
        n = (await db.execute(select(func.count()).select_from(Account).where(Account.api_id == a.id))).scalar() or 0
        if n < best_n:
            best, best_n = a, n
    return best.id if best else None

async def _api_from_meta(db, meta, fallback_id):
    aid = None
    ahash = None
    if isinstance(meta, dict):
        aid = meta.get("app_id") or meta.get("api_id")
        ahash = meta.get("app_hash") or meta.get("api_hash")
    if aid and ahash:
        try:
            aid = int(aid)
        except Exception:
            return fallback_id
        row = (await db.execute(select(ApiCredential).where(ApiCredential.api_id==aid))).scalar_one_or_none()
        if row:
            return row.id
        name = f"导入API-{aid}"
        rec = ApiCredential(name=name, api_id=aid, api_hash=str(ahash))
        db.add(rec)
        await db.flush()
        return rec.id
    return fallback_id

async def import_one(db, phone, sess_src, meta, proxy_id, api_id):
    from clients.manager import ClientManager
    phone = phone if str(phone).startswith("+") else "+" + str(phone).lstrip("+")
    exist = (await db.execute(select(Account).where(Account.phone == phone))).scalar_one_or_none()
    if exist:
        return "skip", f"{phone} 已存在"
    api_id = await _api_from_meta(db, meta, api_id)
    session_name = "imp_" + phone
    dest = os.path.join(SESSIONS, session_name + ".session")
    os.makedirs(SESSIONS, exist_ok=True)
    shutil.copy2(sess_src, dest)
    pr = (await db.execute(select(Proxy).where(Proxy.id == proxy_id))).scalar_one_or_none()
    proxy_str = pr.proxy_str if pr else None
    try:
        await ClientManager.reconnect(session_name, proxy_str)
        cl = await ClientManager.get_client(session_name)
        me = await cl.get_me()
        if not me:
            raise ValueError("get_me 空")
        try:
            await cl.disconnect()
        except Exception:
            pass
    except Exception as e:
        try:
            os.remove(dest)
        except Exception:
            pass
        return "dead", f"{phone} 无法上线: {e}"
    acc = Account(
        phone=phone,
        name=str((meta or {}).get("first_name") or getattr(me,"first_name",None) or phone),
        session_name=session_name,
        api_id=api_id,
        proxy_id=proxy_id,
        is_active=True,
        is_online=False,
        created_at=datetime.utcnow(),
    )
    db.add(acc)
    await db.commit()
    return "ok", phone

async def run_zip_import(db, zip_path, job, proxy_id=None, api_id=None, proxy_ids=None):
    job["status"] = "running"
    job["success"] = job.get("success", 0)
    job["dead"] = 0
    job["failed"] = 0
    job["details"] = []
    ids = [int(x) for x in (proxy_ids or ([proxy_id] if proxy_id else [])) if x]
    if not ids:
        ids = [(await _ensure_pool(db)).id]
    proxy_id = ids[0]
    if not api_id:
        api_id = await _pick_api(db)
    if not api_id:
        job["status"] = "error"
        job["msg"] = "没有可用 API"
        return
    tmp = zip_path + "_dir"
    os.makedirs(tmp, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        pairs = {}
        for root, _, files in os.walk(tmp):
            for f in files:
                base, ext = os.path.splitext(f)
                path = os.path.join(root, f)
                if ext == ".session":
                    pairs.setdefault(base, {})["session"] = path
                elif ext == ".json":
                    pairs.setdefault(base, {})["json"] = path
        items = [(k, v) for k, v in pairs.items() if v.get("session")]
        proxies, counts = await _load_line_counts(db)
        job["total"] = len(items)
        for base, v in items:
            meta = {}
            if v.get("json"):
                try:
                    meta = json.load(open(v["json"], encoding="utf-8"))
                except Exception:
                    meta = {}
            phone = str(meta.get("phone") or base).replace(" ", "")
            try:
                pid = _pick_line(counts)
                if not pid:
                    job['failed'] += 1
                    job['details'].append(f'{phone} 所有线路已满15')
                    continue
                st, msg = await import_one(db, phone, v["session"], meta, pid, api_id)
                if st=='ok':
                    counts[pid]=counts.get(pid,0)+1
                if st == "ok":
                    job["success"] += 1
                elif st == "dead":
                    job["dead"] += 1
                else:
                    job["failed"] += 1
                job["details"].append(f"{phone} {msg}")
            except Exception as e:
                try:
                    await db.rollback()
                except Exception:
                    pass
                job["failed"] += 1
                job["details"].append(f"{phone} {e}")
            await asyncio.sleep(0.02)
        job["status"] = "done"
        job["msg"] = f"完成 成功{job['success']} 失败{job['failed']}"
    except Exception as e:
        try:
            await db.rollback()
        except Exception:
            pass
        job["status"] = "error"
        job["msg"] = str(e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            os.remove(zip_path)
        except Exception:
            pass
