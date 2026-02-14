from __future__ import annotations

import base64
import io
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

import pyotp
import qrcode
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import binance, crypto, db, gate, okx
from .models import (
    BinanceAccountModeRequest,
    BinanceQueryOptions,
    CancelRequest,
    CancelResponse,
    CancelResult,
    CredentialIn,
    CredentialOut,
    GateQueryOptions,
    LoginRequest,
    OkxQueryOptions,
    OrderLookupRequest,
    OrderItem,
    QueryRequest,
    QueryResponse,
    TotpConfirmRequest,
)

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BASE_PATH = os.getenv("APP_BASE_PATH", "/").strip()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("account_manager")
LOG_ORDER_DETAILS = os.getenv("LOG_ORDER_DETAILS", "0") == "1"
LOG_ORDER_JSON = os.getenv("LOG_ORDER_JSON", "1") == "1"
try:
    LOG_ORDER_LIMIT = max(0, int(os.getenv("LOG_ORDER_LIMIT", "20")))
except ValueError:
    LOG_ORDER_LIMIT = 20

app = FastAPI(title="account_manager")
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")
SESSION_COOKIE = "account_manager_session"
SESSION_STORE: dict[str, dict] = {}
TOTP_META_KEY = "totp_secret_enc"
TOTP_ISSUER = "account_manager"

EXCHANGE_BINANCE = "binance"
EXCHANGE_OKX = "okx"
EXCHANGE_GATE = "gate"
SUPPORTED_EXCHANGES = {EXCHANGE_BINANCE, EXCHANGE_OKX, EXCHANGE_GATE}

BINANCE_SOURCES = {
    binance.SOURCE_PAPI_UM,
    binance.SOURCE_PAPI_SPOT,
    binance.SOURCE_FAPI_UM,
    binance.SOURCE_SPOT,
}
OKX_SOURCES = set(okx.SOURCE_TO_INST_TYPE.keys())
GATE_SOURCES = {gate.SOURCE_SPOT, gate.SOURCE_FUTURES}


@app.on_event("startup")
def startup() -> None:
    conn = db.get_conn()
    db.init_db(conn)
    conn.close()
    logger.info("startup complete; base_path=%s", DEFAULT_BASE_PATH or "/")


def session_id_from_request(request: Request) -> str:
    return request.cookies.get(SESSION_COOKIE, "")


def base_path_from_request(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-prefix", "").strip()
    root_path = str(request.scope.get("root_path", "")).strip()
    env_base = DEFAULT_BASE_PATH
    value = forwarded or root_path or env_base
    if not value:
        return ""
    if not value.startswith("/"):
        value = f"/{value}"
    if value != "/" and value.endswith("/"):
        value = value.rstrip("/")
    return "" if value == "/" else value


def path_with_base(request: Request, path: str) -> str:
    base_path = base_path_from_request(request)
    if not path.startswith("/"):
        path = f"/{path}"
    if not base_path:
        return path
    if path == "/":
        return base_path
    return f"{base_path}{path}"


def get_session(request: Request) -> dict:
    session_id = session_id_from_request(request)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not logged in")
    session = SESSION_STORE.get(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    return session


def is_logged_in(request: Request) -> bool:
    session_id = session_id_from_request(request)
    return bool(session_id) and session_id in SESSION_STORE


def get_fernet_from_request(request: Request) -> crypto.Fernet:
    session = get_session(request)
    conn = db.get_conn()
    enabled = db.get_meta(conn, TOTP_META_KEY) is not None
    conn.close()
    if not enabled:
        raise HTTPException(status_code=403, detail="TOTP setup required")
    if not session.get("totp_verified"):
        raise HTTPException(status_code=401, detail="TOTP verification required")
    return session["fernet"]


def mask_key(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def normalize_label(raw: str) -> str:
    return raw.strip()


def normalize_exchange(raw: str) -> str:
    value = raw.lower().strip()
    if value == "okex":
        return EXCHANGE_OKX
    return value


def to_ms(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    num: float
    if isinstance(value, (int, float)):
        num = float(value)
    else:
        try:
            num = float(str(value).strip())
        except Exception:
            return None
    if num > 1_000_000_000_000:
        return int(num)
    if num > 1_000_000_000:
        return int(num * 1000)
    return int(num)


def to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def normalize_order(exchange: str, source: str, raw: dict[str, Any]) -> OrderItem:
    exchange = normalize_exchange(exchange)

    if exchange == EXCHANGE_BINANCE:
        symbol = str(raw.get("symbol", "")).upper()
        order_id = raw.get("orderId") or raw.get("orderID") or raw.get("order_id")
        client_order_id = raw.get("clientOrderId") or raw.get("client_order_id")
        order_type = raw.get("type")
        side = raw.get("side")
        status = raw.get("status")
        price = raw.get("price")
        orig_qty = raw.get("origQty") or raw.get("origQuantity")
        executed_qty = raw.get("executedQty")
        time_value = raw.get("time")
        update_time = raw.get("updateTime")
        position_side = raw.get("positionSide")
        reduce_only = raw.get("reduceOnly")
    elif exchange == EXCHANGE_OKX:
        symbol = str(raw.get("instId", "")).upper()
        order_id = raw.get("ordId") or raw.get("orderId")
        client_order_id = raw.get("clOrdId") or raw.get("clientOrderId")
        order_type = raw.get("ordType") or raw.get("type")
        side = raw.get("side")
        status = raw.get("state") or raw.get("status")
        price = raw.get("px") or raw.get("price")
        orig_qty = raw.get("sz") or raw.get("size")
        executed_qty = raw.get("accFillSz") or raw.get("filledSz") or raw.get("fillSz")
        time_value = raw.get("cTime") or raw.get("createTime")
        update_time = raw.get("uTime") or raw.get("updateTime")
        position_side = raw.get("posSide") or raw.get("positionSide")
        reduce_only = raw.get("reduceOnly")
    elif exchange == EXCHANGE_GATE:
        symbol = str(raw.get("currency_pair") or raw.get("contract") or raw.get("symbol") or "").upper()
        order_id = raw.get("id") or raw.get("order_id") or raw.get("orderId")
        client_order_id = raw.get("text") or raw.get("client_oid") or raw.get("clientOrderId")
        order_type = raw.get("type")
        side = raw.get("side")
        status = raw.get("status")
        price = raw.get("price")
        orig_qty = raw.get("amount") if raw.get("amount") is not None else raw.get("size")
        executed_qty = raw.get("filled_amount")
        if executed_qty is None and raw.get("left") is not None and raw.get("size") is not None:
            try:
                executed_qty = str(abs(float(raw.get("size"))) - abs(float(raw.get("left"))))
            except Exception:
                executed_qty = None
        time_value = (
            raw.get("create_time_ms")
            if raw.get("create_time_ms") is not None
            else raw.get("create_time")
        )
        update_time = (
            raw.get("update_time_ms")
            if raw.get("update_time_ms") is not None
            else raw.get("update_time")
        )
        if update_time is None:
            update_time = raw.get("finish_time")
        position_side = raw.get("position_side")
        reduce_only = raw.get("reduce_only")
    else:
        raise ValueError(f"unsupported exchange: {exchange}")

    order_id_str = str(order_id) if order_id is not None else None
    client_order_id_str = str(client_order_id) if client_order_id is not None else None

    order_key = order_id_str or client_order_id_str or uuid.uuid4().hex
    order_item_id = f"{exchange}:{source}:{symbol}:{order_key}"

    return OrderItem(
        id=order_item_id,
        exchange=exchange,
        source=source,
        symbol=symbol,
        side=str(side) if side is not None else None,
        order_type=str(order_type) if order_type is not None else None,
        status=str(status) if status is not None else None,
        price=str(price) if price is not None else None,
        orig_qty=str(orig_qty) if orig_qty is not None else None,
        executed_qty=str(executed_qty) if executed_qty is not None else None,
        time=to_ms(time_value),
        update_time=to_ms(update_time),
        order_id=order_id_str,
        client_order_id=client_order_id_str,
        position_side=str(position_side) if position_side is not None else None,
        reduce_only=to_bool(reduce_only),
    )


def get_totp_secret(conn, fernet: crypto.Fernet) -> str | None:
    enc = db.get_meta(conn, TOTP_META_KEY)
    if not enc:
        return None
    try:
        return fernet.decrypt(enc.encode("utf-8")).decode("utf-8")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Failed to decrypt TOTP secret") from exc


def load_exchange_credentials(
    conn,
    request: Request,
    exchange: str,
    label: str,
) -> tuple[str, str, str | None]:
    row = db.get_credentials(conn, exchange, label)
    if row is None and exchange == EXCHANGE_OKX:
        row = db.get_credentials(conn, "okex", label)
    if not row:
        raise HTTPException(
            status_code=400,
            detail=f"{exchange.upper()} credentials not set for account '{label}'",
        )
    fernet = get_fernet_from_request(request)
    try:
        api_key = fernet.decrypt(row["api_key_enc"].encode("utf-8")).decode("utf-8")
        api_secret = fernet.decrypt(row["api_secret_enc"].encode("utf-8")).decode("utf-8")
        passphrase_enc = row["api_passphrase_enc"]
        api_passphrase = (
            fernet.decrypt(passphrase_enc.encode("utf-8")).decode("utf-8")
            if passphrase_enc
            else None
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Failed to decrypt credentials") from exc
    return api_key, api_secret, api_passphrase


def validate_exchange_or_400(exchange: str) -> None:
    if exchange not in SUPPORTED_EXCHANGES:
        raise HTTPException(status_code=400, detail=f"Unsupported exchange: {exchange}")


def is_okx_cancel_success(status: int, body: str) -> bool:
    if not (200 <= status < 300):
        return False
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    code = str(parsed.get("code", "")).strip()
    if code not in {"", "0"}:
        return False
    data = parsed.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            s_code = str(item.get("sCode", "")).strip()
            if s_code not in {"", "0"}:
                return False
    return True


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    session_id = session_id_from_request(request)
    if not session_id or session_id not in SESSION_STORE:
        return RedirectResponse(url=path_with_base(request, "/login"))
    session = SESSION_STORE.get(session_id)
    conn = db.get_conn()
    enabled = db.get_meta(conn, TOTP_META_KEY) is not None
    conn.close()
    if not enabled:
        return RedirectResponse(url=path_with_base(request, "/2fa/setup"))
    if not session or not session.get("totp_verified"):
        return RedirectResponse(url=path_with_base(request, "/login"))
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "base_path": base_path_from_request(request)},
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    if is_logged_in(request):
        return RedirectResponse(url=path_with_base(request, "/"))
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "base_path": base_path_from_request(request)},
    )


@app.get("/order_lookup", response_class=HTMLResponse)
def order_lookup_page(request: Request) -> HTMLResponse:
    session_id = session_id_from_request(request)
    if not session_id or session_id not in SESSION_STORE:
        return RedirectResponse(url=path_with_base(request, "/login"))
    session = SESSION_STORE.get(session_id)
    conn = db.get_conn()
    enabled = db.get_meta(conn, TOTP_META_KEY) is not None
    conn.close()
    if not enabled:
        return RedirectResponse(url=path_with_base(request, "/2fa/setup"))
    if not session or not session.get("totp_verified"):
        return RedirectResponse(url=path_with_base(request, "/login"))
    return templates.TemplateResponse(
        "order_lookup.html",
        {"request": request, "base_path": base_path_from_request(request)},
    )


@app.get("/api/session")
def session_status(request: Request) -> dict:
    session_id = session_id_from_request(request)
    session = SESSION_STORE.get(session_id) if session_id else None
    conn = db.get_conn()
    enabled = db.get_meta(conn, TOTP_META_KEY) is not None
    conn.close()
    return {
        "logged_in": bool(session),
        "totp_enabled": enabled,
        "totp_verified": bool(session and session.get("totp_verified")),
    }


@app.post("/api/login")
def login(payload: LoginRequest, response: Response) -> dict:
    master_key = payload.master_key.strip()
    totp_code = payload.totp_code.strip() if payload.totp_code else ""

    conn = db.get_conn()
    salt = db.ensure_kdf_salt(conn)
    fernet = crypto.derive_fernet(master_key, salt)
    sample = conn.execute("SELECT api_key_enc FROM credentials LIMIT 1").fetchone()
    totp_enc = db.get_meta(conn, TOTP_META_KEY)
    if sample is not None:
        try:
            fernet.decrypt(sample["api_key_enc"].encode("utf-8"))
        except Exception as exc:
            conn.close()
            raise HTTPException(status_code=400, detail="Invalid master key") from exc

    if totp_enc is not None:
        if not totp_code:
            conn.close()
            raise HTTPException(status_code=400, detail="TOTP code required")
        secret = get_totp_secret(conn, fernet)
        if not secret:
            conn.close()
            raise HTTPException(status_code=400, detail="TOTP secret missing")
        totp = pyotp.TOTP(secret)
        if not totp.verify(totp_code, valid_window=1):
            conn.close()
            raise HTTPException(status_code=400, detail="Invalid TOTP code")

    conn.close()

    session_id = uuid.uuid4().hex
    SESSION_STORE[session_id] = {
        "fernet": fernet,
        "totp_verified": totp_enc is not None,
        "pending_totp": None,
    }
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response) -> dict:
    session_id = session_id_from_request(request)
    if session_id in SESSION_STORE:
        SESSION_STORE.pop(session_id, None)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/2fa/setup", response_class=HTMLResponse)
def totp_setup_page(request: Request) -> HTMLResponse:
    if not is_logged_in(request):
        return RedirectResponse(url=path_with_base(request, "/login"))
    return templates.TemplateResponse(
        "twofa.html",
        {"request": request, "base_path": base_path_from_request(request)},
    )


@app.get("/api/2fa/status")
def totp_status(request: Request) -> dict:
    session = get_session(request)
    conn = db.get_conn()
    enabled = db.get_meta(conn, TOTP_META_KEY) is not None
    conn.close()
    return {"enabled": enabled, "verified": bool(session.get("totp_verified"))}


@app.post("/api/2fa/setup/start")
def totp_setup_start(request: Request) -> dict:
    session = get_session(request)
    conn = db.get_conn()
    enabled = db.get_meta(conn, TOTP_META_KEY) is not None
    conn.close()
    if enabled and not session.get("totp_verified"):
        raise HTTPException(status_code=401, detail="TOTP verification required")
    secret = pyotp.random_base32()
    session["pending_totp"] = secret

    totp = pyotp.TOTP(secret)
    otpauth_url = totp.provisioning_uri(name="local", issuer_name=TOTP_ISSUER)

    qr = qrcode.make(otpauth_url)
    buffer = io.BytesIO()
    qr.save(buffer, format="PNG")
    qr_data = base64.b64encode(buffer.getvalue()).decode("ascii")
    return {
        "secret": secret,
        "otpauth_url": otpauth_url,
        "qr_data_url": f"data:image/png;base64,{qr_data}",
    }


@app.post("/api/2fa/setup/confirm")
def totp_setup_confirm(payload: TotpConfirmRequest, request: Request) -> dict:
    session = get_session(request)
    conn = db.get_conn()
    enabled = db.get_meta(conn, TOTP_META_KEY) is not None
    conn.close()
    if enabled and not session.get("totp_verified"):
        raise HTTPException(status_code=401, detail="TOTP verification required")
    secret = session.get("pending_totp")
    if not secret:
        raise HTTPException(status_code=400, detail="No pending TOTP setup")
    code = payload.code.strip()
    totp = pyotp.TOTP(secret)
    if not totp.verify(code, valid_window=1):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")

    conn = db.get_conn()
    fernet = session["fernet"]
    enc = fernet.encrypt(secret.encode("utf-8")).decode("utf-8")
    db.set_meta(conn, TOTP_META_KEY, enc)
    conn.close()
    session["pending_totp"] = None
    session["totp_verified"] = True
    return {"ok": True}


@app.get("/api/credentials", response_model=list[CredentialOut])
def list_credentials(request: Request) -> list[CredentialOut]:
    conn = db.get_conn()
    fernet = get_fernet_from_request(request)
    rows = db.list_credentials(conn)
    results: list[CredentialOut] = []
    for row in rows:
        masked = "encrypted"
        try:
            api_key = fernet.decrypt(row["api_key_enc"].encode("utf-8")).decode("utf-8")
            masked = mask_key(api_key)
        except Exception:
            masked = "encrypted"
        results.append(
            CredentialOut(
                exchange=normalize_exchange(row["exchange"]),
                label=row["label"],
                api_key_masked=masked,
                has_passphrase=bool(row["api_passphrase_enc"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )
    conn.close()
    return results


@app.post("/api/credentials", response_model=CredentialOut)
def upsert_credentials(payload: CredentialIn, request: Request) -> CredentialOut:
    exchange = normalize_exchange(payload.exchange)
    validate_exchange_or_400(exchange)

    label = normalize_label(payload.label)
    if not label:
        raise HTTPException(status_code=400, detail="Label is required")

    if exchange == EXCHANGE_OKX and not (payload.api_passphrase or "").strip():
        raise HTTPException(status_code=400, detail="OKX requires api_passphrase")

    conn = db.get_conn()
    fernet = get_fernet_from_request(request)
    api_key_enc = fernet.encrypt(payload.api_key.encode("utf-8")).decode("utf-8")
    api_secret_enc = fernet.encrypt(payload.api_secret.encode("utf-8")).decode("utf-8")
    passphrase_raw = (payload.api_passphrase or "").strip()
    api_passphrase_enc = (
        fernet.encrypt(passphrase_raw.encode("utf-8")).decode("utf-8") if passphrase_raw else None
    )

    db.upsert_credentials(conn, exchange, label, api_key_enc, api_secret_enc, api_passphrase_enc)
    row = db.get_credentials(conn, exchange, label)
    conn.close()
    if not row:
        raise HTTPException(status_code=500, detail="Failed to save credentials")
    return CredentialOut(
        exchange=row["exchange"],
        label=row["label"],
        api_key_masked=mask_key(payload.api_key),
        has_passphrase=bool(row["api_passphrase_enc"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@app.post("/api/binance/account_mode")
def detect_binance_account_mode(payload: BinanceAccountModeRequest, request: Request) -> dict:
    label = normalize_label(payload.account)
    if not label:
        raise HTTPException(status_code=400, detail="Account is required")

    conn = db.get_conn()
    api_key, api_secret, _ = load_exchange_credentials(conn, request, EXCHANGE_BINANCE, label)
    conn.close()

    try:
        result = binance.detect_account_mode(api_key, api_secret)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "account": label,
        "mode": result.get("mode"),
        "via": result.get("via"),
        "papi_status": result.get("papi_status"),
        "fapi_status": result.get("fapi_status"),
    }


@app.post("/api/orders/query", response_model=QueryResponse)
def query_orders(payload: QueryRequest, request: Request) -> QueryResponse:
    exchange = normalize_exchange(payload.exchange)
    validate_exchange_or_400(exchange)

    label = normalize_label(payload.account)
    if not label:
        raise HTTPException(status_code=400, detail="Account is required")

    logger.info(
        "orders_query start exchange=%s account=%s client=%s",
        exchange,
        label,
        request.client.host if request.client else "-",
    )

    conn = db.get_conn()
    api_key, api_secret, api_passphrase = load_exchange_credentials(conn, request, exchange, label)
    conn.close()

    orders: list[OrderItem] = []
    errors: list[str] = []
    source_counts: dict[str, int] = {}

    if exchange == EXCHANGE_BINANCE:
        options = payload.binance or BinanceQueryOptions()
        requested_mode = options.account_mode.strip().upper() if options.account_mode else "AUTO"
        selected_sources = []
        if options.papi_um:
            selected_sources.append(binance.SOURCE_PAPI_UM)
        if options.papi_spot:
            selected_sources.append(binance.SOURCE_PAPI_SPOT)
        if options.fapi_um:
            selected_sources.append(binance.SOURCE_FAPI_UM)
        if options.spot:
            selected_sources.append(binance.SOURCE_SPOT)

        if not selected_sources:
            if requested_mode == binance.ACCOUNT_MODE_UNIFIED:
                selected_sources = [
                    binance.SOURCE_PAPI_UM,
                    binance.SOURCE_PAPI_SPOT,
                    binance.SOURCE_FAPI_UM,
                ]
            elif requested_mode == binance.ACCOUNT_MODE_STANDARD:
                selected_sources = [binance.SOURCE_FAPI_UM, binance.SOURCE_SPOT]
            else:
                detected = binance.detect_account_mode(api_key, api_secret)
                detected_mode = detected.get("mode")
                if detected_mode == binance.ACCOUNT_MODE_UNIFIED:
                    selected_sources = [
                        binance.SOURCE_PAPI_UM,
                        binance.SOURCE_PAPI_SPOT,
                        binance.SOURCE_FAPI_UM,
                    ]
                else:
                    selected_sources = [binance.SOURCE_FAPI_UM, binance.SOURCE_SPOT]

        for source in selected_sources:
            try:
                raw = binance.fetch_open_orders(source, api_key, api_secret)
                source_counts[source] = len(raw)
                orders.extend(normalize_order(exchange, source, item) for item in raw)
            except Exception as exc:
                errors.append(f"{source}: {exc}")
                source_counts[source] = 0
                logger.exception("orders_query failed source=%s account=%s", source, label)

    elif exchange == EXCHANGE_OKX:
        options = payload.okx or OkxQueryOptions()
        selected_sources = []
        if options.swap:
            selected_sources.append(okx.SOURCE_SWAP)
        if options.spot:
            selected_sources.append(okx.SOURCE_SPOT)
        if options.margin:
            selected_sources.append(okx.SOURCE_MARGIN)
        if not selected_sources:
            selected_sources = [okx.SOURCE_SWAP]

        if not api_passphrase:
            raise HTTPException(status_code=400, detail="OKX api_passphrase is required")

        for source in selected_sources:
            try:
                raw = okx.fetch_open_orders(source, api_key, api_secret, api_passphrase)
                source_counts[source] = len(raw)
                orders.extend(normalize_order(exchange, source, item) for item in raw)
            except Exception as exc:
                errors.append(f"{source}: {exc}")
                source_counts[source] = 0
                logger.exception("orders_query failed source=%s account=%s", source, label)

    elif exchange == EXCHANGE_GATE:
        options = payload.gate or GateQueryOptions()
        selected_sources = []
        if options.spot:
            selected_sources.append(gate.SOURCE_SPOT)
        if options.futures:
            selected_sources.append(gate.SOURCE_FUTURES)
        if not selected_sources:
            selected_sources = [gate.SOURCE_SPOT, gate.SOURCE_FUTURES]

        spot_account = (options.spot_account or gate.DEFAULT_SPOT_ACCOUNT).strip() or gate.DEFAULT_SPOT_ACCOUNT
        settle = (options.settle or gate.DEFAULT_SETTLE).strip().lower() or gate.DEFAULT_SETTLE

        for source in selected_sources:
            try:
                raw = gate.fetch_open_orders(
                    source,
                    api_key,
                    api_secret,
                    spot_account=spot_account,
                    settle=settle,
                )
                source_counts[source] = len(raw)
                orders.extend(normalize_order(exchange, source, item) for item in raw)
            except Exception as exc:
                errors.append(f"{source}: {exc}")
                source_counts[source] = 0
                logger.exception("orders_query failed source=%s account=%s", source, label)

    response = QueryResponse(orders=orders, errors=errors)
    logger.info(
        "orders_query done exchange=%s account=%s orders=%s errors=%s sources=%s",
        exchange,
        label,
        len(orders),
        len(errors),
        source_counts,
    )
    if LOG_ORDER_JSON:
        logger.info("orders_query response=%s", json.dumps(response.dict(), ensure_ascii=True))
    if LOG_ORDER_DETAILS and orders:
        detail_count = len(orders) if LOG_ORDER_LIMIT <= 0 else min(len(orders), LOG_ORDER_LIMIT)
        sample = [
            {
                "exchange": order.exchange,
                "source": order.source,
                "symbol": order.symbol,
                "side": order.side,
                "status": order.status,
                "order_id": order.order_id,
            }
            for order in orders[:detail_count]
        ]
        logger.info("orders_query sample count=%s items=%s", detail_count, sample)
    return response


@app.post("/api/orders/cancel", response_model=CancelResponse)
def cancel_orders(payload: CancelRequest, request: Request) -> CancelResponse:
    exchange = normalize_exchange(payload.exchange)
    validate_exchange_or_400(exchange)

    label = normalize_label(payload.account)
    if not label:
        raise HTTPException(status_code=400, detail="Account is required")

    conn = db.get_conn()
    api_key, api_secret, api_passphrase = load_exchange_credentials(conn, request, exchange, label)
    conn.close()

    gate_opts = payload.gate or GateQueryOptions()
    gate_spot_account = (gate_opts.spot_account or gate.DEFAULT_SPOT_ACCOUNT).strip() or gate.DEFAULT_SPOT_ACCOUNT
    gate_settle = (gate_opts.settle or gate.DEFAULT_SETTLE).strip().lower() or gate.DEFAULT_SETTLE

    results: list[CancelResult] = []
    for order in payload.orders:
        if not order.symbol:
            results.append(
                CancelResult(
                    id=order.id,
                    ok=False,
                    status=0,
                    message="missing symbol",
                )
            )
            continue

        try:
            if exchange == EXCHANGE_BINANCE:
                status, body, _headers = binance.cancel_order(
                    order.source,
                    order.symbol,
                    order.order_id,
                    order.client_order_id,
                    api_key,
                    api_secret,
                )
                ok_flag = 200 <= status < 300
            elif exchange == EXCHANGE_OKX:
                if not api_passphrase:
                    raise RuntimeError("OKX api_passphrase is required")
                status, body, _headers = okx.cancel_order(
                    order.source,
                    order.symbol,
                    order.order_id,
                    order.client_order_id,
                    api_key,
                    api_secret,
                    api_passphrase,
                )
                ok_flag = is_okx_cancel_success(status, body)
            else:
                status, body, _headers = gate.cancel_order(
                    order.source,
                    order.symbol,
                    order.order_id,
                    order.client_order_id,
                    api_key,
                    api_secret,
                    spot_account=gate_spot_account,
                    settle=gate_settle,
                )
                ok_flag = 200 <= status < 300

            results.append(
                CancelResult(
                    id=order.id,
                    ok=ok_flag,
                    status=status,
                    message=body,
                )
            )
            if not ok_flag:
                logger.warning(
                    "cancel failed exchange=%s source=%s symbol=%s order_id=%s status=%s",
                    exchange,
                    order.source,
                    order.symbol,
                    order.order_id,
                    status,
                )
        except Exception as exc:
            results.append(
                CancelResult(
                    id=order.id,
                    ok=False,
                    status=0,
                    message=str(exc),
                )
            )
            logger.exception(
                "cancel failed exchange=%s source=%s symbol=%s order_id=%s",
                exchange,
                order.source,
                order.symbol,
                order.order_id,
            )

    return CancelResponse(results=results)


@app.post("/api/orders/lookup", response_model=QueryResponse)
def lookup_order(payload: OrderLookupRequest, request: Request) -> QueryResponse:
    exchange = normalize_exchange(payload.exchange)
    validate_exchange_or_400(exchange)

    label = normalize_label(payload.account)
    if not label:
        raise HTTPException(status_code=400, detail="Account is required")

    source = payload.source.strip()
    if exchange == EXCHANGE_BINANCE and source not in BINANCE_SOURCES:
        raise HTTPException(status_code=400, detail="Unsupported binance source")
    if exchange == EXCHANGE_OKX and source not in OKX_SOURCES:
        raise HTTPException(status_code=400, detail="Unsupported okx source")
    if exchange == EXCHANGE_GATE and source not in GATE_SOURCES:
        raise HTTPException(status_code=400, detail="Unsupported gate source")

    symbol = payload.symbol.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol is required")

    order_id = payload.order_id.strip() if payload.order_id else ""
    client_order_id = payload.client_order_id.strip() if payload.client_order_id else ""
    if exchange == EXCHANGE_GATE:
        if not order_id:
            raise HTTPException(status_code=400, detail="Gate lookup requires order_id")
    else:
        if not order_id and not client_order_id:
            raise HTTPException(status_code=400, detail="Order ID or Client Order ID required")

    logger.info(
        "order_lookup start exchange=%s account=%s source=%s symbol=%s client=%s",
        exchange,
        label,
        source,
        symbol,
        request.client.host if request.client else "-",
    )

    conn = db.get_conn()
    api_key, api_secret, api_passphrase = load_exchange_credentials(conn, request, exchange, label)
    conn.close()

    orders: list[OrderItem] = []
    errors: list[str] = []
    try:
        if exchange == EXCHANGE_BINANCE:
            raw = binance.fetch_order(
                source,
                symbol,
                order_id or None,
                client_order_id or None,
                api_key,
                api_secret,
            )
        elif exchange == EXCHANGE_OKX:
            if not api_passphrase:
                raise RuntimeError("OKX api_passphrase is required")
            raw = okx.fetch_order(
                source,
                symbol,
                order_id or None,
                client_order_id or None,
                api_key,
                api_secret,
                api_passphrase,
            )
        else:
            gate_spot_account = (payload.gate_spot_account or gate.DEFAULT_SPOT_ACCOUNT).strip() or gate.DEFAULT_SPOT_ACCOUNT
            gate_settle = (payload.gate_settle or gate.DEFAULT_SETTLE).strip().lower() or gate.DEFAULT_SETTLE
            raw = gate.fetch_order(
                source,
                symbol,
                order_id or None,
                client_order_id or None,
                api_key,
                api_secret,
                spot_account=gate_spot_account,
                settle=gate_settle,
            )

        orders.append(normalize_order(exchange, source, raw))
    except Exception as exc:
        errors.append(f"{source}: {exc}")
        logger.exception(
            "order_lookup failed exchange=%s source=%s symbol=%s account=%s",
            exchange,
            source,
            symbol,
            label,
        )

    response = QueryResponse(orders=orders, errors=errors)
    if LOG_ORDER_JSON:
        logger.info("order_lookup response=%s", json.dumps(response.dict(), ensure_ascii=True))
    logger.info(
        "order_lookup done exchange=%s account=%s source=%s orders=%s errors=%s",
        exchange,
        label,
        source,
        len(orders),
        len(errors),
    )
    return response
