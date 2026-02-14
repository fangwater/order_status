from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
import urllib.parse

import httpx

BASE_URL = os.environ.get("OKX_BASE_URL", "https://www.okx.com")
SIMULATED_TRADING = os.environ.get("OKX_SIMULATED_TRADING", "0") == "1"

logger = logging.getLogger("account_manager.okx")

SOURCE_SWAP = "okx_swap"
SOURCE_SPOT = "okx_spot"
SOURCE_MARGIN = "okx_margin"

SOURCE_TO_INST_TYPE = {
    SOURCE_SWAP: "SWAP",
    SOURCE_SPOT: "SPOT",
    SOURCE_MARGIN: "MARGIN",
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def json_body(data: Any) -> str:
    if data is None:
        return ""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def sign(timestamp: str, method: str, request_path: str, body: str, secret: str) -> str:
    payload = f"{timestamp}{method.upper()}{request_path}{body}"
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def request_private(
    method: str,
    path: str,
    api_key: str,
    api_secret: str,
    passphrase: str,
    params: Dict[str, Any] | None = None,
    body: Any = None,
    timeout: int = 10,
) -> Tuple[int, str, Dict[str, str]]:
    method = method.upper()
    params = params or {}
    query = urllib.parse.urlencode(sorted((k, str(v)) for k, v in params.items())) if params else ""
    request_path = f"{path}?{query}" if query else path

    body_str = "" if method == "GET" else json_body(body)
    timestamp = utc_timestamp()
    signature = sign(timestamp, method, request_path, body_str, api_secret)

    url = f"{BASE_URL.rstrip('/')}{request_path}"
    headers = {
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }
    if SIMULATED_TRADING:
        headers["x-simulated-trading"] = "1"

    resp = httpx.request(
        method,
        url,
        headers=headers,
        content=None if method == "GET" else body_str.encode("utf-8"),
        timeout=timeout,
    )
    logger.info(
        "okx response method=%s path=%s status=%s body=%s",
        method,
        request_path,
        resp.status_code,
        resp.text,
    )
    return resp.status_code, resp.text, dict(resp.headers)


def parse_okx_response(body_text: str) -> Tuple[bool, str, str, Any]:
    try:
        parsed = json.loads(body_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"unexpected response: {body_text}")
    code = str(parsed.get("code", "")).strip()
    msg = str(parsed.get("msg", "")).strip()
    data = parsed.get("data")
    return code in {"", "0"}, code, msg, data


def fetch_open_orders(
    source: str,
    api_key: str,
    api_secret: str,
    passphrase: str,
) -> List[Dict[str, Any]]:
    if source not in SOURCE_TO_INST_TYPE:
        raise ValueError(f"unsupported source: {source}")

    inst_type = SOURCE_TO_INST_TYPE[source]
    orders: List[Dict[str, Any]] = []
    after: str | None = None

    for _ in range(20):
        params: Dict[str, Any] = {"instType": inst_type, "limit": "100"}
        if after:
            params["after"] = after
        status, body, _ = request_private(
            "GET",
            "/api/v5/trade/orders-pending",
            api_key,
            api_secret,
            passphrase,
            params=params,
        )
        if status != 200:
            raise RuntimeError(f"request failed ({status}): {body}")
        ok, code, msg, data = parse_okx_response(body)
        if not ok:
            raise RuntimeError(f"okx error code={code} msg={msg}")
        if not isinstance(data, list):
            raise RuntimeError(f"unexpected data: {body}")

        batch = [item for item in data if isinstance(item, dict)]
        orders.extend(batch)
        if len(batch) < 100:
            break
        last_ord_id = str(batch[-1].get("ordId", "")).strip() if batch else ""
        if not last_ord_id or last_ord_id == after:
            break
        after = last_ord_id

    return orders


def cancel_order(
    source: str,
    symbol: str,
    order_id: str | None,
    client_order_id: str | None,
    api_key: str,
    api_secret: str,
    passphrase: str,
) -> Tuple[int, str, Dict[str, str]]:
    if source not in SOURCE_TO_INST_TYPE:
        raise ValueError(f"unsupported source: {source}")
    if not symbol:
        raise ValueError("symbol required for okx cancel")

    payload: Dict[str, Any] = {"instId": symbol}
    if order_id:
        payload["ordId"] = order_id
    elif client_order_id:
        payload["clOrdId"] = client_order_id
    else:
        raise ValueError("order_id or client_order_id required")

    return request_private(
        "POST",
        "/api/v5/trade/cancel-order",
        api_key,
        api_secret,
        passphrase,
        body=payload,
    )


def fetch_order(
    source: str,
    symbol: str,
    order_id: str | None,
    client_order_id: str | None,
    api_key: str,
    api_secret: str,
    passphrase: str,
) -> Dict[str, Any]:
    if source not in SOURCE_TO_INST_TYPE:
        raise ValueError(f"unsupported source: {source}")
    if not symbol:
        raise ValueError("symbol required")

    params: Dict[str, Any] = {"instId": symbol}
    if order_id:
        params["ordId"] = order_id
    elif client_order_id:
        params["clOrdId"] = client_order_id
    else:
        raise ValueError("order_id or client_order_id required")

    status, body, _ = request_private(
        "GET",
        "/api/v5/trade/order",
        api_key,
        api_secret,
        passphrase,
        params=params,
    )
    if status != 200:
        raise RuntimeError(f"request failed ({status}): {body}")

    ok, code, msg, data = parse_okx_response(body)
    if not ok:
        raise RuntimeError(f"okx error code={code} msg={msg}")
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected data: {body}")
    if not data:
        raise RuntimeError("order not found")

    item = data[0]
    if not isinstance(item, dict):
        raise RuntimeError(f"unexpected order payload: {body}")
    return item
