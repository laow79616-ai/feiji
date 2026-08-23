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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

@app.get("/")
async def index():
    return FileResponse("static/index.html")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)