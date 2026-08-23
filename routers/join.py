from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from clients.manager import ClientManager

router = APIRouter(prefix="/join", tags=["加入群组/频道"])


class BatchJoinRequest(BaseModel):
    session_names: List[str]   # 要操作的账号 session_name 列表
    links: List[str]           # 群组/频道链接列表


@router.post("/batch")
async def batch_join(req: BatchJoinRequest):
    """
    一键多账号加入多个群组/频道
    支持公开频道（@username 或 t.me/xxx）和私有邀请链接
    """
    if not req.session_names or not req.links:
        raise HTTPException(400, "账号列表和链接列表不能为空")

    results = await ClientManager.batch_join(req.session_names, req.links)
    success_count = sum(1 for r in results if r["success"])
    return {
        "total": len(results),
        "success": success_count,
        "failed": len(results) - success_count,
        "details": results
    }