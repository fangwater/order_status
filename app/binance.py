from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
from typing import Any, Dict, List, Tuple

import httpx

BASE_PAPI_URL = os.environ.get("BINANCE_PAPI_URL", "https://papi.binance.com")
BASE_FAPI_URL = os.environ.get("BINANCE_FAPI_URL", "https://fapi.binance.com")

SOURCE_PAPI_UM = "papi_um"
SOURCE_PAPI_SPOT = "papi_spot"
SOURCE_FAPI_UM = "fapi_um"

OPEN_ORDER_PATHS = {
    SOURCE_PAPI_UM: (BASE_PAPI_URL, "/papi/v1/um/openOrders"),
    SOURCE_PAPI_SPOT: (BASE_PAPI_URL, "/papi/v1/spot/openOrders"),
    SOURCE_FAPI_UM: (BASE_FAPI_URL, "/fapi/v1/openOrders"),
}

CANCEL_ORDER_PATHS = {
    SOURCE_PAPI_UM: (BASE_PAPI_URL, "/papi/v1/um/order"),
    SOURCE_PAPI_SPOT: (BASE_PAPI_URL, "/papi/v1/spot/order"),
    SOURCE_FAPI_UM: (BASE_FAPI_URL, "/fapi/v1/order"),
}


def now_ms() -> int:
    return int(time.time() * 1000)


def sign(query: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()


def build_query(params: Dict[str, Any]) -> str:
    items = sorted(((k, v) for k, v in params.items()), key=lambda kv: kv[0])
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
    order_id: str,
    api_key: str,
    api_secret: str,
) -> Tuple[int, str, Dict[str, str]]:
    if source not in CANCEL_ORDER_PATHS:
        raise ValueError(f"unsupported source: {source}")
    base_url, path = CANCEL_ORDER_PATHS[source]
    params = {"symbol": symbol, "orderId": order_id}
    return request_signed("DELETE", base_url, path, params, api_key, api_secret)
