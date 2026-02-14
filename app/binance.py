from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
from typing import Any, Dict, List, Tuple

import httpx

BASE_PAPI_URL = os.environ.get("BINANCE_PAPI_URL", "https://papi.binance.com")
BASE_FAPI_URL = os.environ.get("BINANCE_FAPI_URL", "https://fapi.binance.com")
BASE_SPOT_URL = os.environ.get("BINANCE_SPOT_URL", "https://api.binance.com")

logger = logging.getLogger("account_manager.binance")

SOURCE_PAPI_UM = "papi_um"
SOURCE_PAPI_SPOT = "papi_spot"
SOURCE_FAPI_UM = "fapi_um"
SOURCE_SPOT = "spot"

ACCOUNT_MODE_UNIFIED = "UNIFIED"
ACCOUNT_MODE_STANDARD = "STANDARD"

OPEN_ORDER_PATHS = {
    SOURCE_PAPI_UM: (BASE_PAPI_URL, "/papi/v1/um/openOrders"),
    SOURCE_PAPI_SPOT: (BASE_PAPI_URL, "/papi/v1/margin/openOrders"),
    SOURCE_FAPI_UM: (BASE_FAPI_URL, "/fapi/v1/openOrders"),
    SOURCE_SPOT: (BASE_SPOT_URL, "/api/v3/openOrders"),
}

CANCEL_ORDER_PATHS = {
    SOURCE_PAPI_UM: (BASE_PAPI_URL, "/papi/v1/um/order"),
    SOURCE_PAPI_SPOT: (BASE_PAPI_URL, "/papi/v1/margin/order"),
    SOURCE_FAPI_UM: (BASE_FAPI_URL, "/fapi/v1/order"),
    SOURCE_SPOT: (BASE_SPOT_URL, "/api/v3/order"),
}

ORDER_QUERY_PATHS = {
    SOURCE_PAPI_UM: (BASE_PAPI_URL, "/papi/v1/um/order"),
    SOURCE_PAPI_SPOT: (BASE_PAPI_URL, "/papi/v1/margin/order"),
    SOURCE_FAPI_UM: (BASE_FAPI_URL, "/fapi/v1/order"),
    SOURCE_SPOT: (BASE_SPOT_URL, "/api/v3/order"),
}


def now_ms() -> int:
    return int(time.time() * 1000)


def sign(query: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()


def build_query(params: Dict[str, Any]) -> str:
    items = sorted(((k, str(v)) for k, v in params.items()), key=lambda kv: kv[0])
    return urllib.parse.urlencode(items, safe="-_.~")


def request_signed(
    method: str,
    base_url: str,
    path: str,
    params: Dict[str, Any],
    api_key: str,
    api_secret: str,
    timeout: int = 10,
) -> Tuple[int, str, Dict[str, str]]:
    q = dict(params)
    q.setdefault("recvWindow", "5000")
    q["timestamp"] = str(now_ms())
    query = build_query(q)
    signature = sign(query, api_secret)
    url = f"{base_url.rstrip('/')}{path}?{query}&signature={signature}"
    headers = {"X-MBX-APIKEY": api_key}
    resp = httpx.request(method, url, headers=headers, timeout=timeout)
    logger.info(
        "binance response method=%s path=%s status=%s body=%s",
        method,
        path,
        resp.status_code,
        resp.text,
    )
    if resp.status_code >= 400:
        body_preview = resp.text
        if len(body_preview) > 500:
            body_preview = f"{body_preview[:500]}..."
        logger.warning(
            "binance request failed method=%s path=%s status=%s body=%s",
            method,
            path,
            resp.status_code,
            body_preview,
        )
    return resp.status_code, resp.text, dict(resp.headers)


def fetch_open_orders(
    source: str,
    api_key: str,
    api_secret: str,
) -> List[Dict[str, Any]]:
    if source not in OPEN_ORDER_PATHS:
        raise ValueError(f"unsupported source: {source}")
    base_url, path = OPEN_ORDER_PATHS[source]
    status, body, _headers = request_signed(
        "GET",
        base_url,
        path,
        {},
        api_key,
        api_secret,
    )
    if status != 200:
        raise RuntimeError(f"request failed ({status}): {body}")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected response: {body}")
    return payload


def cancel_order(
    source: str,
    symbol: str,
    order_id: str | None,
    client_order_id: str | None,
    api_key: str,
    api_secret: str,
) -> Tuple[int, str, Dict[str, str]]:
    if source not in CANCEL_ORDER_PATHS:
        raise ValueError(f"unsupported source: {source}")
    base_url, path = CANCEL_ORDER_PATHS[source]
    params: Dict[str, Any] = {"symbol": symbol}
    if order_id:
        params["orderId"] = order_id
    elif client_order_id:
        params["origClientOrderId"] = client_order_id
    else:
        raise ValueError("order_id or client_order_id required")
    return request_signed("DELETE", base_url, path, params, api_key, api_secret)


def fetch_order(
    source: str,
    symbol: str,
    order_id: str | None,
    client_order_id: str | None,
    api_key: str,
    api_secret: str,
) -> Dict[str, Any]:
    if source not in ORDER_QUERY_PATHS:
        raise ValueError(f"unsupported source: {source}")
    base_url, path = ORDER_QUERY_PATHS[source]
    params: Dict[str, Any] = {"symbol": symbol}
    if order_id:
        params["orderId"] = order_id
    elif client_order_id:
        params["origClientOrderId"] = client_order_id
    else:
        raise ValueError("order_id or client_order_id required")
    status, body, _headers = request_signed(
        "GET",
        base_url,
        path,
        params,
        api_key,
        api_secret,
    )
    if status != 200:
        raise RuntimeError(f"request failed ({status}): {body}")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected response: {body}")
    return payload


def parse_account_type(account_data: Dict[str, Any]) -> str | None:
    visited: set[int] = set()

    def _search(obj: Any, depth: int = 0) -> str | None:
        if depth > 6:
            return None
        obj_id = id(obj)
        if obj_id in visited:
            return None
        visited.add(obj_id)

        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "accountType" and isinstance(value, str) and value.strip():
                    return value.strip().upper()
                if key in {"portfolioMargin", "isPortfolioMargin", "portfolioMarginAccount"}:
                    if isinstance(value, bool) and value:
                        return "PORTFOLIO"
                found = _search(value, depth + 1)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = _search(item, depth + 1)
                if found:
                    return found
        return None

    return _search(account_data)


def detect_account_mode(api_key: str, api_secret: str) -> Dict[str, Any]:
    papi_status, papi_body, _ = request_signed(
        "GET",
        BASE_PAPI_URL,
        "/papi/v1/um/account",
        {},
        api_key,
        api_secret,
    )
    if 200 <= papi_status < 300:
        try:
            data = json.loads(papi_body)
        except json.JSONDecodeError:
            data = {}
        account_type = parse_account_type(data) or ACCOUNT_MODE_UNIFIED
        if account_type == "PORTFOLIO":
            account_mode = ACCOUNT_MODE_UNIFIED
        elif account_type in {ACCOUNT_MODE_UNIFIED, ACCOUNT_MODE_STANDARD}:
            account_mode = account_type
        else:
            account_mode = ACCOUNT_MODE_UNIFIED
        return {
            "mode": account_mode,
            "via": "PAPI",
            "papi_status": papi_status,
            "fapi_status": None,
        }

    fapi_status, fapi_body, _ = request_signed(
        "GET",
        BASE_FAPI_URL,
        "/fapi/v2/account",
        {},
        api_key,
        api_secret,
    )
    if 200 <= fapi_status < 300:
        return {
            "mode": ACCOUNT_MODE_STANDARD,
            "via": "FAPI",
            "papi_status": papi_status,
            "fapi_status": fapi_status,
        }

    papi_preview = papi_body if len(papi_body) <= 500 else f"{papi_body[:500]}..."
    fapi_preview = fapi_body if len(fapi_body) <= 500 else f"{fapi_body[:500]}..."
    raise RuntimeError(
        "unable to detect account mode; "
        f"PAPI status={papi_status} body={papi_preview}; "
        f"FAPI status={fapi_status} body={fapi_preview}"
    )
