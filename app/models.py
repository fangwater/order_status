from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class CredentialIn(BaseModel):
    exchange: str
    label: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)
    api_secret: str = Field(..., min_length=1)
    api_passphrase: str | None = None


class CredentialOut(BaseModel):
    exchange: str
    label: str
    api_key_masked: str
    has_passphrase: bool = False
    created_at: str
    updated_at: str


class BinanceQueryOptions(BaseModel):
    account_mode: str = "AUTO"
    papi_um: bool = True
    papi_spot: bool = True
    fapi_um: bool = True
    spot: bool = False


class OkxQueryOptions(BaseModel):
    swap: bool = True
    spot: bool = False
    margin: bool = False


class GateQueryOptions(BaseModel):
    spot: bool = True
    futures: bool = True
    spot_account: str = "unified"
    settle: str = "usdt"


class QueryRequest(BaseModel):
    exchange: str
    account: str = Field(..., min_length=1)
    binance: BinanceQueryOptions | None = None
    okx: OkxQueryOptions | None = None
    gate: GateQueryOptions | None = None


class OrderLookupRequest(BaseModel):
    exchange: str
    account: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)
    symbol: str = Field(..., min_length=1)
    order_id: str | None = None
    client_order_id: str | None = None
    gate_spot_account: str | None = None
    gate_settle: str | None = None


class OrderItem(BaseModel):
    id: str
    exchange: str
    source: str
    symbol: str
    side: str | None = None
    order_type: str | None = None
    status: str | None = None
    price: str | None = None
    orig_qty: str | None = None
    executed_qty: str | None = None
    time: int | None = None
    update_time: int | None = None
    order_id: str | None = None
    client_order_id: str | None = None
    position_side: str | None = None
    reduce_only: bool | None = None


class QueryResponse(BaseModel):
    orders: List[OrderItem]
    errors: List[str] = Field(default_factory=list)


class OrderRef(BaseModel):
    id: str
    source: str
    symbol: str
    order_id: str | None = None
    client_order_id: str | None = None


class CancelRequest(BaseModel):
    exchange: str
    account: str = Field(..., min_length=1)
    orders: List[OrderRef]
    gate: GateQueryOptions | None = None


class CancelResult(BaseModel):
    id: str
    ok: bool
    status: int
    message: str


class CancelResponse(BaseModel):
    results: List[CancelResult]


class LoginRequest(BaseModel):
    master_key: str = Field(..., min_length=1)
    totp_code: str | None = None


class TotpConfirmRequest(BaseModel):
    code: str = Field(..., min_length=1)


class BinanceAccountModeRequest(BaseModel):
    account: str = Field(..., min_length=1)
