"""FastAPI Web Server for the Windows 98 Retro Bot UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database
from bot_manager import bot_manager

app = FastAPI(title="Binance Retro Trading Bot")

# Enable CORS for local testing if needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://s3.tradingview.com https://*.tradingview.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://*.tradingview.com; "
        "font-src 'self' data: https://fonts.gstatic.com https://win98icons.alexmeub.com; "
        "img-src 'self' data: https://win98icons.alexmeub.com https://*.tradingview.com; "
        "frame-src 'self' https://*.tradingview.com; "
        "connect-src 'self' https://fapi.binance.com https://api.binance.com wss://fstream.binance.com wss://stream.binance.com https://*.tradingview.com wss://*.tradingview.com;"
    )
    return response

# Paths
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)


# Models
class UserRegisterModel(BaseModel):
    username: str
    password: str


class UserLoginModel(BaseModel):
    username: str
    password: str


class ConfigUpdateModel(BaseModel):
    api_key: str | None = None
    api_secret: str | None = None
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    mode: str | None = None
    exchange_env: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    fast_ema: int | None = None
    slow_ema: int | None = None
    leverage: int | None = None
    risk_per_trade: float | None = None
    trade_margin_usdt: float | None = None
    max_daily_loss: float | None = None
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    cooldown_seconds: int | None = None
    paper_initial_balance: float | None = None
    enable_websocket: int | None = None


# Dependency
async def get_current_user(
    session_token: str | None = Cookie(None),
    x_session_token: str | None = Header(None, alias="X-Session-Token"),
) -> dict[str, Any]:
    token = session_token or x_session_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token missing. Please log in.",
        )
    user = database.get_user_by_session(token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session invalid or expired. Please log in again.",
        )
    return user


# Auth Routes
@app.post("/api/auth/register")
def register(data: UserRegisterModel):
    if not data.username or not data.password:
        raise HTTPException(status_code=400, detail="Username and password are required.")
    
    if len(data.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
        
    user_id = database.register_user(data.username, data.password)
    if user_id is None:
        raise HTTPException(status_code=400, detail="Username already exists.")
        
    return {"message": "User registered successfully."}


@app.post("/api/auth/login")
def login(data: UserLoginModel, response: Response):
    user = database.authenticate_user(data.username, data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password.")
        
    token = database.create_session(user["id"])
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        max_age=7 * 24 * 3600,
        samesite="lax",
    )
    return {"token": token, "username": user["username"], "id": user["id"]}


@app.post("/api/auth/logout")
def logout(response: Response, session_token: str | None = Cookie(None)):
    if session_token:
        database.delete_session(session_token)
    response.delete_cookie("session_token")
    return {"message": "Logged out successfully."}


@app.get("/api/auth/me")
def me(current_user: dict[str, Any] = Depends(get_current_user)):
    return current_user


# Config Routes
@app.get("/api/config")
def get_config(current_user: dict[str, Any] = Depends(get_current_user)):
    cfg = database.get_user_config(current_user["id"])
    if not cfg:
        raise HTTPException(status_code=404, detail="Configuration not found.")
    
    # Mask secrets
    if cfg.get("api_key"):
        cfg["api_key"] = cfg["api_key"][:6] + "..." + cfg["api_key"][-4:] if len(cfg["api_key"]) > 10 else "..."
    if cfg.get("api_secret"):
        cfg["api_secret"] = "********"
    if cfg.get("telegram_token"):
        cfg["telegram_token"] = "********"
        
    return cfg


@app.post("/api/config")
def update_config(data: ConfigUpdateModel, current_user: dict[str, Any] = Depends(get_current_user)):
    # Check if the bot is currently running. We should discourage settings updates while bot runs.
    state = bot_manager.get_bot_state(current_user["id"])
    if state.get("is_running"):
        raise HTTPException(status_code=400, detail="Cannot update configuration while the bot is running. Please stop the bot first.")

    updates = data.model_dump(exclude_unset=True)
    
    # Filter out secrets that are still masked (i.e. user did not edit them)
    if updates.get("api_key") and "..." in updates["api_key"]:
        updates.pop("api_key")
    if updates.get("api_secret") == "********":
        updates.pop("api_secret")
    if updates.get("telegram_token") == "********":
        updates.pop("telegram_token")

    database.save_user_config(current_user["id"], updates)
    return {"message": "Configuration updated successfully."}


# Bot Control Routes
@app.post("/api/bot/start")
def start_bot(current_user: dict[str, Any] = Depends(get_current_user)):
    # Verify user has configured api keys if running in live mode
    cfg = database.get_user_config(current_user["id"])
    if not cfg:
        raise HTTPException(status_code=400, detail="Please configure the bot first.")
        
    if cfg.get("mode") == "live" and (not cfg.get("api_key") or not cfg.get("api_secret")):
        raise HTTPException(status_code=400, detail="API Key and API Secret are required for live trading.")

    success = bot_manager.start_bot(current_user["id"])
    if not success:
        raise HTTPException(status_code=500, detail="Failed to start bot.")
    return {"message": "Bot started successfully."}


@app.post("/api/bot/stop")
def stop_bot(current_user: dict[str, Any] = Depends(get_current_user)):
    success = bot_manager.stop_bot(current_user["id"])
    if not success:
        raise HTTPException(status_code=500, detail="Failed to stop bot.")
    return {"message": "Bot stopped successfully."}


@app.get("/api/bot/status")
def get_bot_status(current_user: dict[str, Any] = Depends(get_current_user)):
    return bot_manager.get_bot_state(current_user["id"])


@app.get("/api/bot/logs")
def get_bot_logs(lines: int = 50, current_user: dict[str, Any] = Depends(get_current_user)):
    log_content = bot_manager.get_bot_logs(current_user["id"], lines)
    return {"logs": log_content}


# Dashboard Data (merged)
@app.get("/api/dashboard")
def get_dashboard(current_user: dict[str, Any] = Depends(get_current_user)):
    state = bot_manager.get_bot_state(current_user["id"])
    trades = database.get_user_trades(current_user["id"], limit=50)
    config = database.get_user_config(current_user["id"])
    
    # Strip sensitive fields from config
    if config:
        config.pop("api_key", None)
        config.pop("api_secret", None)
        config.pop("telegram_token", None)

    return {
        "state": state,
        "trades": trades,
        "config": config,
    }


# Auto-resume bots on startup
@app.on_event("startup")
def startup_event():
    database.init_db()
    bot_manager.auto_resume_bots()


# Serve frontend static files
# Place this last so it doesn't hijack API routes
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/{full_path:path}")
def serve_frontend(full_path: str):
    # If API path, return 404
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API endpoint not found.")
        
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "Retro bot web interface is ready. Create index.html in static/."}
