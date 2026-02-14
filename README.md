# account_manager

本项目提供一个本地的多交易所订单管理工具：
- Binance：统一账户（PAPI）UM / Spot(Margin)、普通 UM(FAPI)、普通 Spot(API v3)
- OKX：SWAP / SPOT / Margin 挂单查询、撤单、单号查询
- Gate：Spot / Futures 挂单查询、撤单、单号查询
- 卡片式前端展示、多选/全选、批量撤单

## 运行

1) 创建虚拟环境并安装依赖：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2) 生成主密钥（用于加密保存 API Key/Secret/Passphrase）：

```bash
python - <<'PY'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
PY
```

3) 启动服务：

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 6301
```

或使用 pm2 脚本：

```bash
scripts/start_account_manager.sh
```

4) 打开页面并登录：

```text
http://127.0.0.1:6301/
```

在登录页输入上一步生成的主密钥（仅保存于内存，会话结束需重新登录）。

## Binance 账户模式识别

项目内置了模式识别脚本：

```bash
BINANCE_API_KEY=... BINANCE_API_SECRET=... \
python scripts/check_binance_account_mode.py
```

也可以在页面 Query 面板中点击 `Detect Mode` 自动识别。

## 2FA (Google Authenticator)

登录后需要先完成 2FA Setup（强制），生成二维码并绑定到 Google Authenticator。
启用后登录需要同时输入 TOTP 验证码。

## 可选环境变量

- `ACCOUNT_MANAGER_DB_PATH`: SQLite 文件路径（优先）。
- `ORDER_STATUS_DB_PATH`: 兼容旧变量名。
- `APP_BASE_PATH`: 应用基础前缀，默认 `/`。若走反向代理子路径（如 `/account_manager`），设置为对应前缀并由代理转发到应用根路径。
- `BINANCE_PAPI_URL`: 默认 `https://papi.binance.com`
- `BINANCE_FAPI_URL`: 默认 `https://fapi.binance.com`
- `BINANCE_SPOT_URL`: 默认 `https://api.binance.com`
- `OKX_BASE_URL`: 默认 `https://www.okx.com`
- `OKX_SIMULATED_TRADING`: `1` 时请求头加 `x-simulated-trading: 1`
- `GATE_BASE_URL`: 默认 `https://api.gateio.ws`
- `GATE_SPOT_ACCOUNT`: 默认 `unified`
- `GATE_FUTURES_SETTLE`: 默认 `usdt`

## 说明

- API Key/Secret/Passphrase 仅保存加密后的密文。
- 支持多组账号，通过 `Label` 区分；查询时可按交易所筛选账号。
- 撤单按订单逐条请求；完成后自动刷新。
