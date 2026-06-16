// Windows 98 Single-Page Dashboard Logic

let currentUser = null;
let dashboardInterval = null;
let logsInterval = null;
let activeSymbol = "SOLUSDT";

// On Page Load
document.addEventListener("DOMContentLoaded", () => {
    updateClock();
    setInterval(updateClock, 1000);
    checkAuth();
});

// Tray Clock (just in case they display it or we log it)
function updateClock() {
    // Clock updates could render in status bar if added, for now idle
}

// Authentication Check
async function checkAuth() {
    const token = localStorage.getItem("session_token");
    if (!token) {
        showAuthWindow();
        return;
    }
    
    try {
        const res = await fetch("/api/auth/me", {
            headers: { "X-Session-Token": token }
        });
        if (res.ok) {
            currentUser = await res.json();
            loginSuccess(token);
        } else {
            showAuthWindow();
        }
    } catch (err) {
        console.error("Auth check error:", err);
        showAuthWindow();
    }
}

function showAuthWindow() {
    currentUser = null;
    localStorage.removeItem("session_token");
    document.getElementById("auth-overlay").style.display = "flex";
    document.getElementById("app-container").style.display = "none";
    
    if (dashboardInterval) clearInterval(dashboardInterval);
    if (logsInterval) clearInterval(logsInterval);
}

function loginSuccess(token) {
    localStorage.setItem("session_token", token);
    document.getElementById("auth-overlay").style.display = "none";
    document.getElementById("app-container").style.display = "flex";
    
    document.getElementById("header-username").innerText = currentUser.username;
    
    // Load config settings
    loadConfig();
    
    // Initial chart load
    setTimeout(initSOLChart, 200);
    
    // Start Polling Loops
    startPolling();
}

function switchAuthTab(tab) {
    document.getElementById("tab-login").classList.toggle("active", tab === "login");
    document.getElementById("tab-register").classList.toggle("active", tab === "register");
    
    const submitBtn = document.getElementById("auth-submit-btn");
    if (tab === "register") {
        submitBtn.innerText = "Register";
    } else {
        submitBtn.innerText = "OK";
    }
}

async function handleAuthSubmit(e) {
    e.preventDefault();
    const userEl = document.getElementById("auth-username");
    const passEl = document.getElementById("auth-password");
    const isRegister = document.getElementById("tab-register").classList.contains("active");
    
    const username = userEl.value;
    const password = passEl.value;
    const path = isRegister ? "/api/auth/register" : "/api/auth/login";
    
    try {
        const res = await fetch(path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password })
        });
        
        const data = await res.json();
        
        if (!res.ok) {
            alert("Error: " + (data.detail || "Authentication failed."));
            return;
        }
        
        if (isRegister) {
            alert("Registration successful. Please log in.");
            switchAuthTab("login");
            passEl.value = "";
        } else {
            currentUser = { username: data.username, id: data.id };
            loginSuccess(data.token);
            userEl.value = "";
            passEl.value = "";
        }
    } catch (err) {
        console.error("Auth submit error:", err);
        alert("Failed to reach server.");
    }
}

async function handleLogout() {
    const token = localStorage.getItem("session_token");
    try {
        await fetch("/api/auth/logout", {
            method: "POST",
            headers: { "X-Session-Token": token }
        });
    } catch (e) {}
    showAuthWindow();
}

// Collapsible Panels toggling
function toggleSettingsPanel() {
    const p = document.getElementById("settings-panel");
    if (p) {
        p.style.display = p.style.display === "none" ? "flex" : "none";
    }
}

function toggleLogsPanel() {
    const p = document.getElementById("logs-panel");
    if (p) {
        p.style.display = p.style.display === "none" ? "flex" : "none";
    }
}

// Config Tab Switching
function switchConfigTab(tab) {
    document.querySelectorAll(".win98-tab-header").forEach(h => h.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.style.display = "none");
    
    document.getElementById(`tab-cfg-${tab}`).classList.add("active");
    document.getElementById(`panel-cfg-${tab}`).style.display = "flex";
}

// Config Load & Update
async function loadConfig() {
    const token = localStorage.getItem("session_token");
    try {
        const res = await fetch("/api/config", {
            headers: { "X-Session-Token": token }
        });
        if (!res.ok) return;
        const cfg = await res.json();
        
        // Populate inputs
        document.getElementById("cfg-exchange-env").value = cfg.exchange_env || "demo";
        document.getElementById("cfg-mode").value = cfg.mode || "paper";
        document.getElementById("cfg-api-key").value = cfg.api_key || "";
        document.getElementById("cfg-api-secret").value = cfg.api_secret || "";
        document.getElementById("cfg-telegram-token").value = cfg.telegram_token || "";
        document.getElementById("cfg-telegram-chat-id").value = cfg.telegram_chat_id || "";
        
        document.getElementById("cfg-symbol").value = cfg.symbol || "SOL/USDT:USDT";
        document.getElementById("cfg-timeframe").value = cfg.timeframe || "1m";
        document.getElementById("cfg-fast-ema").value = cfg.fast_ema || 9;
        document.getElementById("cfg-slow-ema").value = cfg.slow_ema || 21;
        document.getElementById("cfg-leverage").value = cfg.leverage || 2;
        document.getElementById("cfg-websocket").checked = !!cfg.enable_websocket;
        
        document.getElementById("cfg-trade-margin").value = cfg.trade_margin_usdt || 10.0;
        document.getElementById("cfg-risk-per-trade").value = cfg.risk_per_trade || 0.01;
        document.getElementById("cfg-stop-loss").value = cfg.stop_loss_pct || 0.01;
        document.getElementById("cfg-take-profit").value = cfg.take_profit_pct || 0.02;
        document.getElementById("cfg-max-daily-loss").value = cfg.max_daily_loss || 0.03;
        document.getElementById("cfg-cooldown").value = cfg.cooldown_seconds || 300;
        document.getElementById("cfg-paper-balance").value = cfg.paper_initial_balance || 1000.0;
        
        // Update active symbol
        const parsedSym = getBinanceSymbol(cfg.symbol);
        if (parsedSym !== activeSymbol) {
            activeSymbol = parsedSym;
            initSOLChart();
        }
    } catch (err) {
        console.error("Load config error:", err);
    }
}

async function handleConfigSubmit(e) {
    e.preventDefault();
    const token = localStorage.getItem("session_token");
    
    const body = {
        exchange_env: document.getElementById("cfg-exchange-env").value,
        mode: document.getElementById("cfg-mode").value,
        api_key: document.getElementById("cfg-api-key").value,
        api_secret: document.getElementById("cfg-api-secret").value,
        telegram_token: document.getElementById("cfg-telegram-token").value,
        telegram_chat_id: document.getElementById("cfg-telegram-chat-id").value,
        symbol: document.getElementById("cfg-symbol").value,
        timeframe: document.getElementById("cfg-timeframe").value,
        fast_ema: parseInt(document.getElementById("cfg-fast-ema").value),
        slow_ema: parseInt(document.getElementById("cfg-slow-ema").value),
        leverage: parseInt(document.getElementById("cfg-leverage").value),
        enable_websocket: document.getElementById("cfg-websocket").checked ? 1 : 0,
        trade_margin_usdt: parseFloat(document.getElementById("cfg-trade-margin").value),
        risk_per_trade: parseFloat(document.getElementById("cfg-risk-per-trade").value),
        stop_loss_pct: parseFloat(document.getElementById("cfg-stop-loss").value),
        take_profit_pct: parseFloat(document.getElementById("cfg-take-profit").value),
        max_daily_loss: parseFloat(document.getElementById("cfg-max-daily-loss").value),
        cooldown_seconds: parseInt(document.getElementById("cfg-cooldown").value),
        paper_initial_balance: parseFloat(document.getElementById("cfg-paper-balance").value),
    };
    
    try {
        const res = await fetch("/api/config", {
            method: "POST",
            headers: { 
                "Content-Type": "application/json",
                "X-Session-Token": token
            },
            body: JSON.stringify(body)
        });
        
        const data = await res.json();
        if (res.ok) {
            alert("Settings saved successfully.");
            loadConfig();
        } else {
            alert("Error: " + (data.detail || "Failed to update settings."));
        }
    } catch (err) {
        console.error("Config save error:", err);
        alert("Failed to connect to server.");
    }
}

// Bot Control (Start / Stop)
async function toggleBot() {
    const btn = document.getElementById("btn-toggle-bot");
    const token = localStorage.getItem("session_token");
    const badge = document.getElementById("status-badge");
    const isRunning = badge.innerText === "RUNNING";
    const action = isRunning ? "stop" : "start";
    
    btn.disabled = true;
    btn.innerText = isRunning ? "Stopping..." : "Starting...";
    
    try {
        const res = await fetch(`/api/bot/${action}`, {
            method: "POST",
            headers: { "X-Session-Token": token }
        });
        
        const data = await res.json();
        if (!res.ok) {
            alert("Control Error: " + (data.detail || "Action failed."));
        }
    } catch (err) {
        console.error("Toggle bot error:", err);
        alert("Failed to connect to server.");
    } finally {
        btn.disabled = false;
        pollDashboard();
    }
}

// Polling loops
function startPolling() {
    pollDashboard();
    pollLogs();
    
    dashboardInterval = setInterval(pollDashboard, 2500);
    logsInterval = setInterval(pollLogs, 3000);
}

async function pollDashboard() {
    if (!currentUser) return;
    const token = localStorage.getItem("session_token");
    
    try {
        const res = await fetch("/api/dashboard", {
            headers: { "X-Session-Token": token }
        });
        if (!res.ok) {
            if (res.status === 401) showAuthWindow();
            return;
        }
        
        const data = await res.json();
        updateDashboardUI(data);
    } catch (err) {
        console.error("Poll dashboard error:", err);
    }
}

function updateDashboardUI(data) {
    const state = data.state;
    const trades = data.trades;
    const config = data.config;
    
    // Status Badge & Button
    const badge = document.getElementById("status-badge");
    const powerBtn = document.getElementById("btn-toggle-bot");
    
    if (state.is_running) {
        badge.innerText = "RUNNING";
        badge.className = "badge-running";
        powerBtn.innerText = "Stop Bot";
        powerBtn.className = "win98-button red-glow";
    } else {
        badge.innerText = "STOPPED";
        badge.className = "badge-stopped";
        powerBtn.innerText = "Start Bot";
        powerBtn.className = "win98-button green-glow";
    }
    
    // Stats formatting
    const pnlEl = document.getElementById("stat-pnl");
    pnlEl.innerText = `${state.realized_pnl.toFixed(2)} USDT`;
    if (state.realized_pnl > 0) {
        pnlEl.className = "stat-value text-bold text-green";
    } else if (state.realized_pnl < 0) {
        pnlEl.className = "stat-value text-bold text-red";
    } else {
        pnlEl.className = "stat-value text-bold";
    }
    
    document.getElementById("stat-winrate").innerText = `${state.win_rate.toFixed(2)}%`;
    document.getElementById("stat-trades").innerText = state.total_trades;
    
    // Metrics
    document.getElementById("stat-price").innerText = state.price > 0 ? state.price.toFixed(4) : "0.0000";
    document.getElementById("stat-balance").innerText = `${state.balance.toFixed(2)} USDT`;
    document.getElementById("stat-uptime").innerText = state.uptime;
    
    // Position
    const posContainer = document.getElementById("position-container");
    if (state.position_side && state.position_side !== "none") {
        const isLong = state.position_side.toLowerCase() === "long";
        const sideColor = isLong ? "text-green" : "text-red";
        posContainer.innerHTML = `
            <div>Side: <strong class="${sideColor}">${state.position_side.toUpperCase()}</strong></div>
            <div>Amount: <strong>${state.position_amount.toFixed(5)}</strong></div>
            <div>Entry: <strong>${state.position_entry.toFixed(4)}</strong></div>
        `;
    } else {
        posContainer.innerHTML = "No active position.";
    }
    
    // Status Bar
    if (config) {
        document.getElementById("status-bar-symbol").innerText = `Symbol: ${config.symbol}`;
        const parsedSym = getBinanceSymbol(config.symbol);
        if (parsedSym !== activeSymbol) {
            activeSymbol = parsedSym;
            initSOLChart();
        }
    }
    
    document.getElementById("status-bar-signal").innerText = `Signal: ${state.signal}`;
    if (state.last_update) {
        const d = new Date(state.last_update);
        document.getElementById("status-bar-updated").innerText = `Last Updated: ${d.toLocaleTimeString()}`;
    }
    
    // Closed Trades Log
    const tableBody = document.getElementById("trades-table-body");
    tableBody.innerHTML = "";
    if (trades && trades.length > 0) {
        trades.forEach(t => {
            const tr = document.createElement("tr");
            const sideClass = t.side.toLowerCase() === "long" ? "text-green text-bold" : "text-red text-bold";
            const pnlClass = t.pnl > 0 ? "text-green" : (t.pnl < 0 ? "text-red" : "");
            
            tr.innerHTML = `
                <td>${config ? config.symbol.split(":")[0] : "SOL/USDT"}</td>
                <td class="${sideClass}">${t.side.toUpperCase()}</td>
                <td>${t.entry_price.toFixed(4)}</td>
                <td>${t.exit_price.toFixed(4)}</td>
                <td>${t.amount.toFixed(4)}</td>
                <td class="${pnlClass}">${t.pnl.toFixed(2)}</td>
                <td>${t.reason}</td>
            `;
            tableBody.appendChild(tr);
        });
    } else {
        tableBody.innerHTML = `<tr><td colspan="7" class="text-center">No trades logged yet.</td></tr>`;
    }

    // Technical Indicators Updates
    if (config) {
        const fastPeriodEl = document.getElementById("ind-fast-period");
        const slowPeriodEl = document.getElementById("ind-slow-period");
        if (fastPeriodEl) fastPeriodEl.innerText = config.fast_ema || 9;
        if (slowPeriodEl) slowPeriodEl.innerText = config.slow_ema || 21;
    }

    if (state.indicators) {
        const ind = state.indicators;
        const currentPrice = state.price || 0;

        // Fast EMA
        const fastValEl = document.getElementById("ind-fast-val");
        const fastBiasEl = document.getElementById("ind-fast-bias");
        if (fastValEl) {
            fastValEl.innerText = ind.fast_ema > 0 ? ind.fast_ema.toFixed(4) : "0.0000";
        }
        if (fastBiasEl) {
            if (ind.fast_ema > 0 && currentPrice > 0) {
                const isBullish = currentPrice > ind.fast_ema;
                fastBiasEl.innerText = isBullish ? "BULLISH" : "BEARISH";
                fastBiasEl.className = isBullish ? "text-green text-bold" : "text-red text-bold";
            } else {
                fastBiasEl.innerText = "-";
                fastBiasEl.className = "";
            }
        }

        // Slow EMA
        const slowValEl = document.getElementById("ind-slow-val");
        const slowBiasEl = document.getElementById("ind-slow-bias");
        if (slowValEl) {
            slowValEl.innerText = ind.slow_ema > 0 ? ind.slow_ema.toFixed(4) : "0.0000";
        }
        if (slowBiasEl) {
            if (ind.slow_ema > 0 && currentPrice > 0) {
                const isBullish = currentPrice > ind.slow_ema;
                slowBiasEl.innerText = isBullish ? "BULLISH" : "BEARISH";
                slowBiasEl.className = isBullish ? "text-green text-bold" : "text-red text-bold";
            } else {
                slowBiasEl.innerText = "-";
                slowBiasEl.className = "";
            }
        }

        // EMA Cross
        const crossValEl = document.getElementById("ind-emacross-val");
        const crossBiasEl = document.getElementById("ind-emacross-bias");
        if (crossValEl) {
            crossValEl.innerText = ind.ema_cross || "-";
            if (ind.ema_cross && ind.ema_cross !== "-") {
                const isBullish = ind.ema_cross.toUpperCase() === "BULLISH";
                crossValEl.className = isBullish ? "text-green text-bold" : "text-red text-bold";
            } else {
                crossValEl.className = "text-bold";
            }
        }
        if (crossBiasEl) {
            if (ind.ema_cross && ind.ema_cross !== "-") {
                const isBullish = ind.ema_cross.toUpperCase() === "BULLISH";
                crossBiasEl.innerText = isBullish ? "BULLISH" : "BEARISH";
                crossBiasEl.className = isBullish ? "text-green text-bold" : "text-red text-bold";
            } else {
                crossBiasEl.innerText = "-";
                crossBiasEl.className = "";
            }
        }

        // RSI
        const rsiValEl = document.getElementById("ind-rsi-val");
        const rsiBiasEl = document.getElementById("ind-rsi-bias");
        if (rsiValEl) {
            rsiValEl.innerText = ind.rsi > 0 ? ind.rsi.toFixed(2) : "0.00";
        }
        if (rsiBiasEl) {
            if (ind.rsi > 0) {
                if (ind.rsi > 70) {
                    rsiBiasEl.innerText = "OVERBOUGHT";
                    rsiBiasEl.className = "text-red text-bold";
                } else if (ind.rsi < 30) {
                    rsiBiasEl.innerText = "OVERSOLD";
                    rsiBiasEl.className = "text-green text-bold";
                } else {
                    const isBullish = ind.rsi > 50;
                    rsiBiasEl.innerText = isBullish ? "BULLISH" : "BEARISH";
                    rsiBiasEl.className = isBullish ? "text-green text-bold" : "text-red text-bold";
                }
            } else {
                rsiBiasEl.innerText = "-";
                rsiBiasEl.className = "";
            }
        }

        // MACD
        const macdValEl = document.getElementById("ind-macd-val");
        const macdBiasEl = document.getElementById("ind-macd-bias");
        if (macdValEl) {
            macdValEl.innerText = (ind.macd !== undefined && ind.macd !== null) ? ind.macd.toFixed(4) : "0.0000";
        }
        if (macdBiasEl) {
            if (ind.macd !== undefined && ind.macd !== null && ind.macd !== 0) {
                const isBullish = ind.macd > 0;
                macdBiasEl.innerText = isBullish ? "BULLISH" : "BEARISH";
                macdBiasEl.className = isBullish ? "text-green text-bold" : "text-red text-bold";
            } else {
                macdBiasEl.innerText = "-";
                macdBiasEl.className = "";
            }
        }
    }
}

async function pollLogs() {
    if (!currentUser) return;
    const token = localStorage.getItem("session_token");
    
    const logsPanel = document.getElementById("logs-panel");
    if (!logsPanel || logsPanel.style.display === "none") return;
    
    try {
        const res = await fetch("/api/bot/logs?lines=60", {
            headers: { "X-Session-Token": token }
        });
        if (!res.ok) return;
        const data = await res.json();
        
        const termEl = document.getElementById("terminal-content");
        termEl.innerText = data.logs || "Waiting for log inputs...";
        
        const screenEl = document.getElementById("terminal-screen");
        screenEl.scrollTop = screenEl.scrollHeight;
    } catch (err) {
        console.error("Poll logs error:", err);
    }
}

function clearLogs() {
    document.getElementById("terminal-content").innerText = "Screen cleared.\n";
}

function downloadLogs() {
    const text = document.getElementById("terminal-content").innerText;
    const blob = new Blob([text], { type: "text/plain" });
    const anchor = document.createElement("a");
    anchor.download = "dos_terminal.log";
    anchor.href = window.URL.createObjectURL(blob);
    anchor.click();
}

function getBinanceSymbol(ccxtSymbol) {
    if (!ccxtSymbol) return "SOLUSDT";
    return ccxtSymbol.split(":")[0].replace("/", "").toUpperCase();
}

// SOL candlestick chart using public free TradingView Widget
function initSOLChart() {
    const container = document.getElementById("chart-container");
    if (!container) return;
    
    container.innerHTML = `<div id="tradingview_chart" style="width: 100%; height: 100%;"></div>`;
    const symbolStr = "BINANCE:" + activeSymbol;
    
    if (typeof TradingView === "undefined") {
        const script = document.createElement("script");
        script.type = "text/javascript";
        script.src = "https://s3.tradingview.com/tv.js";
        script.onload = () => {
            renderTVWidget(symbolStr);
        };
        document.head.appendChild(script);
    } else {
        renderTVWidget(symbolStr);
    }
}

function renderTVWidget(symbolStr) {
    if (typeof TradingView !== "undefined" && TradingView.widget) {
        new TradingView.widget({
            "width": "100%",
            "height": "100%",
            "symbol": symbolStr,
            "interval": "1",
            "timezone": "Etc/UTC",
            "theme": "dark",
            "style": "1",
            "locale": "en",
            "enable_publishing": false,
            "hide_side_toolbar": false,
            "allow_symbol_change": true,
            "container_id": "tradingview_chart"
        });
    }
}
