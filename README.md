# order_status

本项目提供一个本地的订单查询/撤单小工具：
- 支持 Binance 统一账户（PAPI）的 U 本位合约与现货挂单查询
- 支持 Binance 普通 U 本位合约挂单查询
- 卡片式前端展示、多选/全选、批量撤单、撤单结果动画反馈

> OKEX 暂不实现（界面里保留入口但不可用）。

## 运行

1) 创建虚拟环境并安装依赖：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2) 生成主密钥（用于加密保存 API Key/Secret）：

```bash
python - <<'PY'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
PY
```

```bash
3) 启动服务：

```bash
uvicorn app.main:app --reload --port 8088
```

4) 打开页面并登录：

```
http://127.0.0.1:8088/
```

在登录页输入上一步生成的主密钥（仅保存于内存，会话结束需重新登录）。

## 2FA (Google Authenticator)

登录后需要先完成 2FA Setup（强制），生成二维码并绑定到 Google Authenticator。
启用后登录需要同时输入 TOTP 验证码。

## 可选环境变量

- `ORDER_STATUS_DB_PATH`: SQLite 文件路径，默认 `data/order_status.db`。
- `BINANCE_PAPI_URL`: 统一账户接口地址，默认 `https://papi.binance.com`。
- `BINANCE_FAPI_URL`: U 本位合约接口地址，默认 `https://fapi.binance.com`。

## 说明

- API Key/Secret 仅保存加密后的密文。
- 支持多组 Binance 账号，通过 `Label` 区分；查询时选择账号。
- 撤单会逐条请求；完成后自动刷新。
# order_status
