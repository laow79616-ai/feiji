from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from database import engine, Base
from routers import accounts, join, chat, targets, pools
import uvicorn

app = FastAPI(title="Telegram 水军管理系统", version="2.0")

app.include_router(accounts.router)
app.include_router(join.router)
app.include_router(chat.router)
app.include_router(targets.router)
app.include_router(pools.router)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.on_event("startup")
async def startup():
    import asyncio
    from routers.targets import resume_join_jobs
    asyncio.create_task(resume_join_jobs())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

@app.get("/")
async def index():
    return FileResponse("static/index.html")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


from fastapi import Form, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from auth_util import ADMIN_USER, ADMIN_PASS, _make_token, check_token, LOGIN_HTML, _valid_tokens

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # 放行登录与静态探测
    if path in ("/login", "/logout") or path.startswith("/docs") or path.startswith("/openapi"):
        return await call_next(request)
    token = request.cookies.get("tg_token")
    if not token or not check_token(token):
        if path.startswith("/accounts") or path.startswith("/targets") or path.startswith("/pools") or path.startswith("/media"):
            return HTMLResponse(LOGIN_HTML.replace("__ERR__", "请先登录"), status_code=401)
        if path == "/" or path.endswith(".html"):
            return RedirectResponse("/login", status_code=302)
        return HTMLResponse(LOGIN_HTML.replace("__ERR__", "请先登录"), status_code=401)
    return await call_next(request)

@app.get("/login")
async def login_page():
    return HTMLResponse(LOGIN_HTML.replace("__ERR__", ""))

@app.post("/login")
async def login_submit(username: str = Form(...), password: str = Form(...)):
    if username == ADMIN_USER and password == ADMIN_PASS:
        token = _make_token()
        resp = RedirectResponse("/", status_code=302)
        resp.set_cookie("tg_token", token, httponly=True, samesite="lax", max_age=7*24*3600)
        return resp
    return HTMLResponse(LOGIN_HTML.replace("__ERR__", "用户名或密码错误"), status_code=401)

@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("tg_token")
    if token and token in _valid_tokens:
        _valid_tokens.discard(token)
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("tg_token")
    return resp

