from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional
import os
import uuid
from clients.manager import ClientManager

router = APIRouter(prefix="/chat", tags=["聊天与资料"])

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


class SendMessageRequest(BaseModel):
    session_name: str
    chat_id: int | str
    text: str


class UpdateProfileRequest(BaseModel):
    session_name: str
    first_name: Optional[str] = None
    about: Optional[str] = None


@router.post("/send")
async def send_message(req: SendMessageRequest):
    try:
        await ClientManager.send_message(req.session_name, req.chat_id, req.text)
        return {"success": True}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/dialogs/{session_name}")
async def get_dialogs(session_name: str, limit: int = 30):
    try:
        dialogs = await ClientManager.get_dialogs(session_name, limit)
        return {"dialogs": dialogs}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/messages/{session_name}/{chat_id}")
async def get_messages(session_name: str, chat_id: int, limit: int = 40):
    try:
        messages = await ClientManager.get_messages(session_name, chat_id, limit)
        return {"messages": messages}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/profile/update")
async def update_profile(req: UpdateProfileRequest):
    try:
        await ClientManager.update_profile(
            req.session_name,
            first_name=req.first_name,
            about=req.about
        )
        return {"success": True, "msg": "资料已更新"}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/profile/photo")
async def upload_photo(
    session_name: str = Form(...),
    file: UploadFile = File(...)
):
    try:
        # 保存临时文件
        ext = os.path.splitext(file.filename)[1] or ".jpg"
        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = os.path.join(UPLOAD_DIR, filename)
        
        with open(filepath, "wb") as f:
            content = await file.read()
            f.write(content)
        
        await ClientManager.upload_profile_photo(session_name, filepath)
        
        # 可选：删除临时文件
        try:
            os.remove(filepath)
        except:
            pass
            
        return {"success": True, "msg": "头像已更新"}
    except Exception as e:
        raise HTTPException(400, str(e))