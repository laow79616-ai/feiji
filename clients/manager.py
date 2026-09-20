import os
import asyncio
import re
from typing import Dict, Optional, List
from telethon import TelegramClient, events
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    UserAlreadyParticipantError,
    InviteHashExpiredError,
    InviteHashInvalidError
)
from config import API_ID, API_HASH, SESSIONS_DIR



DEAD_SESSION_KEYS = (
    "SESSION_REVOKED",
    "AUTH_KEY",
    "authorization key",
    "two different IP",
    "two different IP addresses",
    "Session 无效",
    "USER_DEACTIVATED",
    "deactivated",
    "unauthorized",
)

def is_dead_session_error(msg: str) -> bool:
    s = (msg or "").lower()
    return any(k.lower() in s for k in DEAD_SESSION_KEYS)

clients: Dict[str, TelegramClient] = {}
code_cache: Dict[str, List[dict]] = {}


def parse_proxy(proxy_str: str):
    """返回 Telethon 可用的 proxy 元组: (type, addr, port, rdns, user, pwd)"""
    if not proxy_str:
        return None
    try:
        import socks
    except Exception:
        socks = None
    s = proxy_str.strip()
    ptype = None
    if socks:
        ptype = socks.SOCKS5
    else:
        ptype = 2  # socks.SOCKS5
    addr = port = user = pwd = None
    if "://" in s:
        from urllib.parse import urlparse, unquote
        u = urlparse(s)
        addr = u.hostname
        port = u.port
        user = unquote(u.username) if u.username else None
        pwd = unquote(u.password) if u.password else None
        scheme = (u.scheme or "socks5").lower()
        if socks:
            if "http" in scheme:
                ptype = socks.HTTP
            elif "socks4" in scheme:
                ptype = socks.SOCKS4
            else:
                ptype = socks.SOCKS5
    else:
        parts = [x.strip() for x in s.split(":")]
        if len(parts) == 4:
            addr, port, user, pwd = parts[0], int(parts[1]), parts[2], parts[3]
        elif len(parts) == 2:
            addr, port = parts[0], int(parts[1])
        else:
            return None
        port = int(port)
    if not addr or not port:
        return None
    return (ptype, addr, int(port), True, user, pwd)


class ClientManager:

    @staticmethod
    def get_session_path(session_name: str) -> str:
        return os.path.join(SESSIONS_DIR, f"{session_name}.session")

    @classmethod
    async def resolve_api(cls, session_name: str):
        api_id, api_hash = API_ID, API_HASH
        try:
            from sqlalchemy import select
            from models import Account, ApiCredential
            factory = None
            try:
                from database import AsyncSessionLocal as factory
            except Exception:
                try:
                    from database import async_session as factory
                except Exception:
                    factory = None
            if factory:
                async with factory() as db:
                    r = await db.execute(select(Account).where(Account.session_name == session_name))
                    acc = r.scalar_one_or_none()
                    if acc and acc.api_id:
                        r2 = await db.execute(select(ApiCredential).where(ApiCredential.id == acc.api_id))
                        cred = r2.scalar_one_or_none()
                        if cred:
                            api_id, api_hash = cred.api_id, cred.api_hash
        except Exception:
            pass
        return api_id, api_hash

    @classmethod
    async def create_client(cls, session_name: str, proxy_str: str = None) -> TelegramClient:
        session_path = cls.get_session_path(session_name)
        proxy = parse_proxy(proxy_str)
        api_id, api_hash = await cls.resolve_api(session_name)
        client = TelegramClient(session_path, api_id, api_hash, proxy=proxy)
        return client

    @classmethod
    async def start_client(cls, session_name: str, phone: str = None, proxy_str: str = None) -> TelegramClient:
        if session_name in clients and clients[session_name].is_connected():
            return clients[session_name]
        client = await cls.create_client(session_name, proxy_str)
        await client.connect()
        if not await client.is_user_authorized():
            if not phone:
                raise ValueError("账号未登录，需要提供手机号")
            await client.send_code_request(phone)
            clients[session_name] = client
            return client
        clients[session_name] = client
        cls._register_handlers(client, session_name)
        return client

    @classmethod
    def _register_handlers(cls, client: TelegramClient, session_name: str):
        if getattr(client, "_tg_handlers_ok", False):
            return
        @client.on(events.NewMessage(incoming=True))
        async def handler(event):
            text = event.message.message or ""
            codes = re.findall(r'\b\d{4,8}\b', text)
            if codes:
                code_info = {
                    "code": codes[0],
                    "from": event.sender_id,
                    "text": text[:200],
                    "time": event.message.date.isoformat()
                }
                if session_name not in code_cache:
                    code_cache[session_name] = []
                code_cache[session_name].insert(0, code_info)
                code_cache[session_name] = code_cache[session_name][:20]
                print(f"[{session_name}] 收到验证码: {codes[0]}")

    @classmethod
    async def login_with_code(cls, session_name: str, phone: str, code: str, password: str = None, proxy_str: str = None):
        client = clients.get(session_name)
        if not client:
            client = await cls.create_client(session_name, proxy_str)
            await client.connect()
            clients[session_name] = client
        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            if not password:
                raise ValueError("该账号开启了两步验证，需要输入二次密码")
            await client.sign_in(password=password)
        cls._register_handlers(client, session_name)
        return client

    @classmethod
    async def get_client(cls, session_name: str) -> Optional[TelegramClient]:
        return clients.get(session_name)

    @classmethod
    async def reconnect(cls, session_name: str, proxy_str: str = None) -> TelegramClient:
        old = clients.get(session_name)
        if old:
            try:
                if old.is_connected():
                    await old.disconnect()
            except Exception:
                pass
            clients.pop(session_name, None)
        client = await cls.create_client(session_name, proxy_str)
        await client.connect()
        if not await client.is_user_authorized():
            raise ValueError("Session 无效，需要重新登录（授权已失效，请重新导入或验证码登录）")
        clients[session_name] = client
        cls._register_handlers(client, session_name)
        return client

    @classmethod
    async def join_group_or_channel(cls, session_name: str, link: str) -> dict:
        client = await cls.get_client(session_name)
        if not client or not client.is_connected():
            try:
                # 尝试带代理重连
                proxy_str = None
                try:
                    from database import async_session
                    from models import Account, Proxy
                    from sqlalchemy import select
                    async with async_session() as db:
                        r = await db.execute(
                            select(Account, Proxy)
                            .join(Proxy, Account.proxy_id == Proxy.id, isouter=True)
                            .where(Account.session_name == session_name)
                        )
                        row = r.first()
                        if row:
                            acc, proxy = row
                            proxy_str = proxy.proxy_str if proxy else None
                except Exception:
                    pass
                if proxy_str:
                    await cls.reconnect(session_name, proxy_str)
                else:
                    await cls.reconnect(session_name)
                client = await cls.get_client(session_name)
            except Exception as e:
                return {"success": False, "msg": f"连接失败: {e}"}
        if not client or not client.is_connected():
            return {"success": False, "msg": "客户端未连接"}
        try:
            if "joinchat/" in link or "+" in link:
                if "joinchat/" in link:
                    hash_code = link.split("joinchat/")[-1]
                else:
                    hash_code = link.split("+")[-1]
                await client(ImportChatInviteRequest(hash_code))
            else:
                username = link.replace("https://t.me/", "").replace("http://t.me/", "").replace("t.me/", "").replace("@", "").strip("/")
                username = username.split("?")[0].strip()
                entity = await client.get_entity(username)
                await client(JoinChannelRequest(entity))
            return {"success": True, "msg": "加入成功"}
        except UserAlreadyParticipantError:
            return {"success": True, "msg": "已经是成员"}
        except (InviteHashExpiredError, InviteHashInvalidError):
            return {"success": False, "msg": "邀请链接无效或已过期"}
        except FloodWaitError as e:
            return {"success": False, "msg": f"触发限制，请等待 {e.seconds} 秒"}
        except Exception as e:
            return {"success": False, "msg": str(e)}


    @classmethod
    async def batch_join(cls, session_names: list, links: list, interval: int = 180):
        """按间隔陆续加入，返回每个账号每个链接的状态"""
        results = []
        for session_name in session_names:
            for link in links:
                result = await cls.join_group_or_channel(session_name, link)
                results.append({
                    "account": session_name,
                    "link": link,
                    **result
                })
                await asyncio.sleep(max(1, int(interval)))
        return results


    @classmethod
    async def send_message(cls, session_name: str, chat_id: str | int, text: str):
        client = await cls.get_client(session_name)
        if not client:
            raise ValueError("客户端不存在")
        try:
            entity = await client.get_entity(chat_id)
            await client.send_message(entity, text)
        except Exception:
            await client.send_message(chat_id, text)
        return True


    @classmethod
    async def inspect_account(cls, session_name: str) -> dict:
        client = await cls.get_client(session_name)
        if not client or not client.is_connected():
            return {"status": "offline", "msg": "未连接"}
        try:
            me = await client.get_me()
        except Exception as e:
            msg = str(e)
            if "frozen" in msg.lower():
                return {"status": "frozen", "msg": "冻结"}
            if any(k in msg.lower() for k in ["deactivated", "revoked", "auth_key", "unauthorized"]):
                return {"status": "dead", "msg": msg}
            return {"status": "error", "msg": msg}
        if getattr(me, "deleted", False):
            return {"status": "dead", "msg": "已注销"}
        if getattr(me, "restricted", False):
            return {"status": "frozen", "msg": "受限/冻结"}
        try:
            from telethon.tl.functions.account import GetAccountTTLRequest
            await client(GetAccountTTLRequest())
        except Exception as e:
            msg = str(e)
            if "frozen" in msg.lower():
                return {"status": "frozen", "msg": "冻结"}
            if "flood" in msg.lower():
                return {"status": "active", "msg": "限流但仍可用"}
        return {"status": "active", "msg": "正常", "phone": getattr(me, "phone", None)}

    @classmethod
    async def get_recent_codes(cls, session_name: str) -> List[dict]:
        return code_cache.get(session_name, [])


    @classmethod
    async def _ensure_client(cls, session_name: str):
        client = await cls.get_client(session_name)
        if client and client.is_connected():
            return client
        proxy_str = None
        try:
            from database import AsyncSessionLocal
            from models import Account, Proxy
            from sqlalchemy import select
            async with AsyncSessionLocal() as db:
                r = await db.execute(
                    select(Account, Proxy)
                    .join(Proxy, Account.proxy_id == Proxy.id, isouter=True)
                    .where(Account.session_name == session_name)
                )
                row = r.first()
                if row:
                    acc, proxy = row
                    proxy_str = proxy.proxy_str if proxy else None
        except Exception:
            try:
                from database import get_db
            except Exception:
                pass
        await cls.reconnect(session_name, proxy_str)
        return await cls.get_client(session_name)

    @classmethod
    async def update_profile(cls, session_name: str, first_name: str = None, about: str = None):
        client = await cls._ensure_client(session_name)
        if not client or not client.is_connected():
            raise ValueError("客户端不存在或未连接")
        from telethon.tl.functions.account import UpdateProfileRequest
        await client(UpdateProfileRequest(
            first_name=first_name or None,
            about=about if about is not None else None
        ))
        return True

    @classmethod
    async def upload_profile_photo(cls, session_name: str, photo_path: str):
        client = await cls._ensure_client(session_name)
        if not client or not client.is_connected():
            raise ValueError("客户端不存在或未连接")
        if not photo_path or not os.path.exists(photo_path):
            raise ValueError("图片文件不存在: " + str(photo_path))
        from telethon.tl.functions.photos import UploadProfilePhotoRequest
        file = await client.upload_file(photo_path)
        try:
            await client(UploadProfilePhotoRequest(file=file))
        except TypeError:
            await client(UploadProfilePhotoRequest(File=file))
        return True

    @classmethod
    async def get_dialogs(cls, session_name: str, limit: int = 30):
        client = await cls.get_client(session_name)
        if not client:
            raise ValueError("客户端不存在或未连接")
        dialogs = await client.get_dialogs(limit=limit)
        result = []
        for d in dialogs:
            result.append({
                "id": d.id,
                "name": d.name or "未知",
                "unread": d.unread_count,
                "is_group": d.is_group,
                "is_channel": d.is_channel,
                "date": d.date.isoformat() if d.date else None
            })
        return result

    @classmethod
    async def get_messages(cls, session_name: str, chat_id: int, limit: int = 30):
        client = await cls.get_client(session_name)
        if not client:
            raise ValueError("客户端不存在或未连接")
        
        try:
            entity = await client.get_entity(chat_id)
            messages = await client.get_messages(entity, limit=limit)
        except Exception:
            messages = await client.get_messages(chat_id, limit=limit)
        
        result = []
        for m in reversed(messages):
            item = {
                "id": m.id,
                "text": m.message or "",
                "out": m.out,
                "date": m.date.isoformat() if m.date else None,
                "sender_id": m.sender_id,
                "media_type": None,
                "media_url": None
            }
            
            if m.photo:
                item["media_type"] = "photo"
                item["text"] = (item["text"] or "") + " 📷"
            elif m.video or (m.document and 'video' in str(getattr(m.document, 'mime_type', ''))):
                item["media_type"] = "video"
                item["text"] = (item["text"] or "") + " 🎬"
            elif m.document:
                item["media_type"] = "file"
                item["text"] = (item["text"] or "") + " 📎"
            
            result.append(item)
        return result

    @classmethod
    async def leave_group_or_channel(cls, session_name: str, link: str) -> dict:
        client = await cls.get_client(session_name)
        if not client or not client.is_connected():
            try:
                await cls.reconnect(session_name)
                client = await cls.get_client(session_name)
            except Exception as e:
                return {"success": False, "msg": f"连接失败: {e}"}
        if not client or not client.is_connected():
            return {"success": False, "msg": "客户端未连接"}
        try:
            if "joinchat/" in link or "+" in link:
                return {"success": False, "msg": "邀请链接无法直接退出，请用用户名链接"}
            username = link.replace("https://t.me/", "").replace("t.me/", "").replace("@", "").strip("/")
            entity = await client.get_entity(username)
            await client.delete_dialog(entity)
            return {"success": True, "msg": "已退出"}
        except Exception as e:
            msg = str(e)
            if "not a member" in msg.lower() or "USER_NOT_PARTICIPANT" in msg:
                return {"success": True, "msg": "已不在群内"}
            return {"success": False, "msg": msg}

    @classmethod
    async def batch_leave(cls, session_names: list, links: list, interval: int = 5):
        results = []
        for session_name in session_names:
            for link in links:
                result = await cls.leave_group_or_channel(session_name, link)
                results.append({"account": session_name, "link": link, **result})
                await asyncio.sleep(max(1, int(interval)))
        return results

    @classmethod
    async def resolve_peer(cls, session_name: str, q: str):
        client = await cls.get_client(session_name)
        if not client or not client.is_connected():
            raise ValueError("客户端未连接，请先整组上线")
        raw = (q or "").strip()
        raw = raw.replace("https://", "").replace("http://", "")
        raw = raw.replace("t.me/", "").replace("telegram.me/", "").lstrip("@").strip("/")
        if not raw:
            raise ValueError("搜索为空")
        # 常见官方号大小写
        aliases = {
            "botfather": "BotFather",
            "bot_father": "BotFather",
        }
        candidates = [raw]
        if raw.lower() in aliases:
            candidates.append(aliases[raw.lower()])
        if raw != raw.capitalize():
            candidates.append(raw[:1].upper()+raw[1:])
        if raw.lower()!=raw:
            candidates.append(raw.lower())
        last=None
        entity=None
        for cand in candidates:
            try:
                entity = await client.get_entity(cand)
                raw=cand
                break
            except Exception as e:
                last=e
                continue
        if entity is None:
            raise last or ValueError("未找到用户")
        name = getattr(entity, "title", None) or " ".join(filter(None, [
            getattr(entity, "first_name", None), getattr(entity, "last_name", None)
        ])).strip() or getattr(entity, "username", None) or str(entity.id)
        return {"id": int(entity.id), "name": name, "username": getattr(entity, "username", None)}

