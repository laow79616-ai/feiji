import os

# ==================== 必须修改 ====================
# 去 https://my.telegram.org 登录后申请
API_ID = 31968604
API_HASH = "2097edbd37f68e6c151450743ad0a207"
# =================================================

# 数据目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

DATABASE_URL = "sqlite+aiosqlite:///./telegram_manager.db"