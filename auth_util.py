
import hashlib
import secrets
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse

# 登录账号（可后续改环境变量）
ADMIN_USER = "admin"
ADMIN_PASS = "Ab123987"
# 简单 token 存内存（重启后需重新登录）
_valid_tokens = set()

def _make_token():
    t = secrets.token_hex(32)
    _valid_tokens.add(t)
    return t

def check_token(token: str) -> bool:
    return token in _valid_tokens

LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>TG Manager 登录</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-white min-h-screen flex items-center justify-center">
<div class="w-full max-w-sm p-6 rounded-xl bg-slate-900 border border-slate-700">
  <h1 class="text-xl font-semibold mb-4 text-center">TG 水军管理系统</h1>
  <form method="post" action="/login" class="space-y-3">
    <input name="username" placeholder="用户名" class="w-full px-3 py-2 rounded bg-slate-800 border border-slate-600" required/>
    <input name="password" type="password" placeholder="密码" class="w-full px-3 py-2 rounded bg-slate-800 border border-slate-600" required/>
    <button class="w-full py-2 rounded bg-blue-600 hover:bg-blue-500">登录</button>
  </form>
  <p class="text-xs text-red-400 mt-3 text-center">__ERR__</p>
</div>
</body>
</html>
"""
