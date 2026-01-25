from __future__ import annotations

import base64
import io
import json
import logging
import os
import uuid
from pathlib import Path

import pyotp
import qrcode
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import binance, crypto, db
from .models import (
    CancelRequest,
    CancelResponse,
    CancelResult,
    CredentialIn,
    CredentialOut,
    LoginRequest,
    OrderLookupRequest,
    OrderItem,
    TotpConfirmRequest,
    QueryRequest,
    QueryResponse,
)

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BASE_PATH = os.getenv("APP_BASE_PATH", "/order_status").strip()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("order_status")
LOG_ORDER_DETAILS = os.getenv("LOG_ORDER_DETAILS", "0") == "1"
LOG_ORDER_JSON = os.getenv("LOG_ORDER_JSON", "1") == "1"
try:
    LOG_ORDER_LIMIT = max(0, int(os.getenv("LOG_ORDER_LIMIT", "20")))
except ValueError:
    LOG_ORDER_LIMIT = 20

app = FastAPI(title="order_status")
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")
SESSION_COOKIE = "order_status_session"
SESSION_STORE: dict[str, dict] = {}
TOTP_META_KEY = "totp_secret_enc"
TOTP_ISSUER = "order_status"


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


def normalize_order(source: str, raw: dict) -> OrderItem:
    symbol = str(raw.get("symbol", "")).upper()
    order_id = raw.get("orderId") or raw.get("orderID") or raw.get("order_id")
    client_order_id = raw.get("clientOrderId") or raw.get("client_order_id")
    order_id_str = str(order_id) if order_id is not None else None
    client_order_id_str = str(client_order_id) if client_order_id is not None else None

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

    order_key = order_id_str or client_order_id_str or uuid.uuid4().hex
    order_item_id = f"{source}:{symbol}:{order_key}"

    return OrderItem(
        id=order_item_id,
        exchange="binance",
        source=source,
        symbol=symbol,
        side=side,
        order_type=order_type,
        status=status,
        price=str(price) if price is not None else None,
        orig_qty=str(orig_qty) if orig_qty is not None else None,
        executed_qty=str(executed_qty) if executed_qty is not None else None,
        time=int(time_value) if isinstance(time_value, (int, float)) else None,
        update_time=int(update_time) if isinstance(update_time, (int, float)) else None,
        order_id=order_id_str,
        client_order_id=client_order_id_str,
        position_side=position_side,
        reduce_only=reduce_only if isinstance(reduce_only, bool) else None,
    )


def normalize_label(raw: str) -> str:
    return raw.strip()


def get_totp_secret(conn, fernet: crypto.Fernet) -> str | None:
    enc = db.get_meta(conn, TOTP_META_KEY)
    if not enc:
        return None
    try:
        return fernet.decrypt(enc.encode("utf-8")).decode("utf-8")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Failed to decrypt TOTP secret") from exc


def load_binance_credentials(conn, request: Request, label: str) -> tuple[str, str]:
    row = db.get_credentials(conn, "binance", label)
    if not row:
        raise HTTPException(
            status_code=400,
            detail=f"Binance credentials not set for account '{label}'",
        )
    fernet = get_fernet_from_request(request)
    try:
        api_key = fernet.decrypt(row["api_key_enc"].encode("utf-8")).decode("utf-8")
        api_secret = fernet.decrypt(row["api_secret_enc"].encode("utf-8")).decode("utf-8")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Failed to decrypt credentials") from exc
    return api_key, api_secret


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
                exchange=row["exchange"],
                label=row["label"],
                api_key_masked=masked,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )
    conn.close()
    return results


@app.post("/api/credentials", response_model=CredentialOut)
def upsert_credentials(payload: CredentialIn, request: Request) -> CredentialOut:
    exchange = payload.exchange.lower().strip()
    if exchange != "binance":
        raise HTTPException(status_code=400, detail="Only binance is supported for now")

    label = normalize_label(payload.label)
    if not label:
        raise HTTPException(status_code=400, detail="Label is required")

    conn = db.get_conn()
    fernet = get_fernet_from_request(request)
    api_key_enc = fernet.encrypt(payload.api_key.encode("utf-8")).decode("utf-8")
    api_secret_enc = fernet.encrypt(payload.api_secret.encode("utf-8")).decode("utf-8")

    db.upsert_credentials(conn, exchange, label, api_key_enc, api_secret_enc)
    row = db.get_credentials(conn, exchange, label)
    conn.close()
    if not row:
        raise HTTPException(status_code=500, detail="Failed to save credentials")
    return CredentialOut(
        exchange=row["exchange"],
        label=row["label"],
        api_key_masked=mask_key(payload.api_key),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@app.post("/api/orders/query", response_model=QueryResponse)
def query_orders(payload: QueryRequest, request: Request) -> QueryResponse:
    exchange = payload.exchange.lower().strip()
    if exchange != "binance":
        raise HTTPException(status_code=400, detail="Only binance is supported for now")
    if payload.binance is None:
        raise HTTPException(status_code=400, detail="Missing binance options")
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
    api_key, api_secret = load_binance_credentials(conn, request, label)
    conn.close()

    orders: list[OrderItem] = []
    errors: list[str] = []
    source_counts: dict[str, int] = {}

    if payload.binance.papi_um:
        try:
            raw = binance.fetch_open_orders(binance.SOURCE_PAPI_UM, api_key, api_secret)
            source_counts[binance.SOURCE_PAPI_UM] = len(raw)
            orders.extend(normalize_order(binance.SOURCE_PAPI_UM, item) for item in raw)
        except Exception as exc:
            errors.append(f"papi_um: {exc}")
            source_counts[binance.SOURCE_PAPI_UM] = 0
            logger.exception(
                "orders_query failed source=%s account=%s",
                binance.SOURCE_PAPI_UM,
                label,
            )

    if payload.binance.papi_spot:
        try:
            raw = binance.fetch_open_orders(binance.SOURCE_PAPI_SPOT, api_key, api_secret)
            source_counts[binance.SOURCE_PAPI_SPOT] = len(raw)
            orders.extend(normalize_order(binance.SOURCE_PAPI_SPOT, item) for item in raw)
        except Exception as exc:
            errors.append(f"papi_spot: {exc}")
            source_counts[binance.SOURCE_PAPI_SPOT] = 0
            logger.exception(
                "orders_query failed source=%s account=%s",
                binance.SOURCE_PAPI_SPOT,
                label,
            )

    if payload.binance.fapi_um:
        try:
            raw = binance.fetch_open_orders(binance.SOURCE_FAPI_UM, api_key, api_secret)
            source_counts[binance.SOURCE_FAPI_UM] = len(raw)
            orders.extend(normalize_order(binance.SOURCE_FAPI_UM, item) for item in raw)
        except Exception as exc:
            errors.append(f"fapi_um: {exc}")
            source_counts[binance.SOURCE_FAPI_UM] = 0
            logger.exception(
                "orders_query failed source=%s account=%s",
                binance.SOURCE_FAPI_UM,
                label,
            )

    response = QueryResponse(orders=orders, errors=errors)
    logger.info(
        "orders_query done account=%s orders=%s errors=%s sources=%s",
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
    exchange = payload.exchange.lower().strip()
    if exchange != "binance":
        raise HTTPException(status_code=400, detail="Only binance is supported for now")
    label = normalize_label(payload.account)
    if not label:
        raise HTTPException(status_code=400, detail="Account is required")

    conn = db.get_conn()
    api_key, api_secret = load_binance_credentials(conn, request, label)
    conn.close()

    results: list[CancelResult] = []
    for order in payload.orders:
        if not order.order_id or not order.symbol:
            results.append(
                CancelResult(
                    id=order.id,
                    ok=False,
                    status=0,
                    message="missing symbol or order_id",
                )
            )
            continue
        try:
            status, body, _headers = binance.cancel_order(
                order.source,
                order.symbol,
                order.order_id,
                api_key,
                api_secret,
            )
            ok = 200 <= status < 300
            results.append(
                CancelResult(
                    id=order.id,
                    ok=ok,
                    status=status,
                    message=body,
                )
            )
            if not ok:
                logger.warning(
                    "cancel failed source=%s symbol=%s order_id=%s status=%s",
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
                "cancel failed source=%s symbol=%s order_id=%s",
                order.source,
                order.symbol,
                order.order_id,
            )

    return CancelResponse(results=results)


@app.post("/api/orders/lookup", response_model=QueryResponse)
def lookup_order(payload: OrderLookupRequest, request: Request) -> QueryResponse:
    exchange = payload.exchange.lower().strip()
    if exchange != "binance":
        raise HTTPException(status_code=400, detail="Only binance is supported for now")
    label = normalize_label(payload.account)
    if not label:
        raise HTTPException(status_code=400, detail="Account is required")

    source = payload.source.strip()
    if source not in {binance.SOURCE_PAPI_UM, binance.SOURCE_PAPI_SPOT, binance.SOURCE_FAPI_UM}:
        raise HTTPException(status_code=400, detail="Unsupported api source")

    symbol = payload.symbol.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol is required")

    order_id = payload.order_id.strip() if payload.order_id else ""
    client_order_id = payload.client_order_id.strip() if payload.client_order_id else ""
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
    api_key, api_secret = load_binance_credentials(conn, request, label)
    conn.close()

    orders: list[OrderItem] = []
    errors: list[str] = []
    try:
        raw = binance.fetch_order(
            source,
            symbol,
            order_id or None,
            client_order_id or None,
            api_key,
            api_secret,
        )
        orders.append(normalize_order(source, raw))
    except Exception as exc:
        errors.append(f"{source}: {exc}")
        logger.exception(
            "order_lookup failed source=%s symbol=%s account=%s",
            source,
            symbol,
            label,
        )

    response = QueryResponse(orders=orders, errors=errors)
    if LOG_ORDER_JSON:
        logger.info("order_lookup response=%s", json.dumps(response.dict(), ensure_ascii=True))
    logger.info(
        "order_lookup done account=%s source=%s orders=%s errors=%s",
        label,
        source,
        len(orders),
        len(errors),
    )
    return response
