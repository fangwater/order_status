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

BASE_URL = os.environ.get("GATE_BASE_URL", "https://api.gateio.ws")
API_PREFIX = "/api/v4"
DEFAULT_SPOT_ACCOUNT = os.environ.get("GATE_SPOT_ACCOUNT", "unified")
DEFAULT_SETTLE = os.environ.get("GATE_FUTURES_SETTLE", "usdt")

logger = logging.getLogger("account_manager.gate")

SOURCE_SPOT = "gate_spot"
SOURCE_FUTURES = "gate_futures"


def build_query(params: Dict[str, Any]) -> str:
    items: List[Tuple[str, Any]] = []
    for key, value in params.items():
        if value is None or value == "":
            continue
        items.append((key, value))
    items.sort(key=lambda kv: kv[0])
    return urllib.parse.urlencode(items, doseq=True)


def sign_request(
    secret: str,
    method: str,
    path: str,
    query: str,
    body: str,
    timestamp: int,
) -> str:
    body_hash = hashlib.sha512(body.encode("utf-8")).hexdigest()
    payload = f"{method}\n{path}\n{query}\n{body_hash}\n{timestamp}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha512).hexdigest()
    return signature


def request_signed(
    method: str,
    path: str,
    api_key: str,
    api_secret: str,
    params: Dict[str, Any] | None = None,
    body_obj: Any = None,
    timeout: int = 10,
) -> Tuple[int, str, Dict[str, str]]:
    method = method.upper()
    params = params or {}
    query = build_query(params)
    body = "" if body_obj is None else json.dumps(body_obj, ensure_ascii=False, separators=(",", ":"))

    full_path = f"{API_PREFIX}{path}"
    ts = int(time.time())
    signature = sign_request(api_secret, method, full_path, query, body, ts)

    url = f"{BASE_URL.rstrip('/')}{full_path}"
    if query:
        url = f"{url}?{query}"

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "KEY": api_key,
        "Timestamp": str(ts),
        "SIGN": signature,
    }

    resp = httpx.request(
        method,
        url,
        headers=headers,
        content=body.encode("utf-8") if body else None,
        timeout=timeout,
    )
    logger.info(
        "gate response method=%s path=%s status=%s body=%s",
        method,
        f"{full_path}?{query}" if query else full_path,
        resp.status_code,
        resp.text,
    )
    return resp.status_code, resp.text, dict(resp.headers)


def parse_json(body: str) -> Any:
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON: {exc}") from exc


def fetch_open_orders(
    source: str,
    api_key: str,
    api_secret: str,
    spot_account: str | None = None,
    settle: str | None = None,
) -> List[Dict[str, Any]]:
    if source == SOURCE_SPOT:
        return fetch_spot_open_orders(
            api_key,
            api_secret,
            spot_account=spot_account or DEFAULT_SPOT_ACCOUNT,
        )
    if source == SOURCE_FUTURES:
        return fetch_futures_open_orders(
            api_key,
            api_secret,
            settle=(settle or DEFAULT_SETTLE).lower(),
        )
    raise ValueError(f"unsupported source: {source}")


def fetch_spot_open_orders(
    api_key: str,
    api_secret: str,
    spot_account: str,
) -> List[Dict[str, Any]]:
    orders: List[Dict[str, Any]] = []
    page = 1
    while True:
        params = {"page": page, "limit": 100, "account": spot_account}
        status, body, _ = request_signed(
            "GET",
            "/spot/open_orders",
            api_key,
            api_secret,
            params=params,
        )
        if status != 200:
            raise RuntimeError(f"request failed ({status}): {body}")

        parsed = parse_json(body)
        if isinstance(parsed, dict):
            batch = parsed.get("orders", [])
        elif isinstance(parsed, list):
            batch = parsed
        else:
            raise RuntimeError(f"unexpected response: {body}")

        if not isinstance(batch, list):
            raise RuntimeError(f"unexpected response: {body}")
        normalized_batch = [item for item in batch if isinstance(item, dict)]

        if not normalized_batch:
            break

        orders.extend(normalized_batch)
        if len(normalized_batch) < 100:
            break
        page += 1

    return orders


def fetch_futures_open_orders(
    api_key: str,
    api_secret: str,
    settle: str,
) -> List[Dict[str, Any]]:
    orders: List[Dict[str, Any]] = []
    page = 1
    while True:
        params = {"status": "open", "page": page, "limit": 100}
        status, body, _ = request_signed(
            "GET",
            f"/futures/{settle}/orders",
            api_key,
            api_secret,
            params=params,
        )
        if status != 200:
            raise RuntimeError(f"request failed ({status}): {body}")

        parsed = parse_json(body)
        if not isinstance(parsed, list):
            raise RuntimeError(f"unexpected response: {body}")
        batch = [item for item in parsed if isinstance(item, dict)]

        if not batch:
            break

        orders.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    return orders


def cancel_order(
    source: str,
    symbol: str,
    order_id: str | None,
    client_order_id: str | None,
    api_key: str,
    api_secret: str,
    spot_account: str | None = None,
    settle: str | None = None,
) -> Tuple[int, str, Dict[str, str]]:
    _ = client_order_id
    if not order_id:
        raise ValueError("order_id required for gate cancel")

    if source == SOURCE_SPOT:
        if not symbol:
            raise ValueError("symbol required for gate spot cancel")
        params = {
            "currency_pair": symbol,
            "account": spot_account or DEFAULT_SPOT_ACCOUNT,
        }
        return request_signed(
            "DELETE",
            f"/spot/orders/{order_id}",
            api_key,
            api_secret,
            params=params,
        )

    if source == SOURCE_FUTURES:
        settle_value = (settle or DEFAULT_SETTLE).lower()
        params = {"contract": symbol} if symbol else {}
        return request_signed(
            "DELETE",
            f"/futures/{settle_value}/orders/{order_id}",
            api_key,
            api_secret,
            params=params,
        )

    raise ValueError(f"unsupported source: {source}")


def fetch_order(
    source: str,
    symbol: str,
    order_id: str | None,
    client_order_id: str | None,
    api_key: str,
    api_secret: str,
    spot_account: str | None = None,
    settle: str | None = None,
) -> Dict[str, Any]:
    _ = client_order_id
    if not order_id:
        raise ValueError("order_id required for gate lookup")

    if source == SOURCE_SPOT:
        if not symbol:
            raise ValueError("symbol required for gate spot lookup")
        params = {
            "currency_pair": symbol,
            "account": spot_account or DEFAULT_SPOT_ACCOUNT,
        }
        status, body, _ = request_signed(
            "GET",
            f"/spot/orders/{order_id}",
            api_key,
            api_secret,
            params=params,
        )
        if status != 200:
            raise RuntimeError(f"request failed ({status}): {body}")
        parsed = parse_json(body)
        if not isinstance(parsed, dict):
            raise RuntimeError(f"unexpected response: {body}")
        return parsed

    if source == SOURCE_FUTURES:
        settle_value = (settle or DEFAULT_SETTLE).lower()
        params = {"contract": symbol} if symbol else {}
        status, body, _ = request_signed(
            "GET",
            f"/futures/{settle_value}/orders/{order_id}",
            api_key,
            api_secret,
            params=params,
        )
        if status != 200:
            raise RuntimeError(f"request failed ({status}): {body}")
        parsed = parse_json(body)
        if not isinstance(parsed, dict):
            raise RuntimeError(f"unexpected response: {body}")
        return parsed

    raise ValueError(f"unsupported source: {source}")
