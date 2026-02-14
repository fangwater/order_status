const state = {
  orders: [],
  selected: new Set(),
  cardMap: new Map(),
  lastQuery: null,
  credentials: [],
};

const basePath = window.APP_BASE_PATH || "";
function withBase(path) {
  if (!path) {
    return basePath || "/";
  }
  const normalized = path.startsWith("/") ? path : `/${path}`;
  if (!basePath) {
    return normalized;
  }
  if (normalized === "/") {
    return basePath;
  }
  return `${basePath}${normalized}`;
}

const credStatus = document.getElementById("credStatus");
const credForm = document.getElementById("credForm");
const credExchange = document.getElementById("exchange");
const apiPassphraseInput = document.getElementById("apiPassphrase");

const queryExchange = document.getElementById("queryExchange");
const accountSelect = document.getElementById("accountSelect");

const binanceOptions = document.getElementById("binanceOptions");
const okxOptions = document.getElementById("okxOptions");
const gateOptions = document.getElementById("gateOptions");

const binanceMode = document.getElementById("binanceMode");
const detectBinanceModeBtn = document.getElementById("detectBinanceModeBtn");
const binanceModeHint = document.getElementById("binanceModeHint");

const ordersGrid = document.getElementById("ordersGrid");
const ordersHint = document.getElementById("ordersHint");
const selectAll = document.getElementById("selectAll");
const selectionMeta = document.getElementById("selectionMeta");
const refreshBtn = document.getElementById("refreshBtn");
const refreshBtn2 = document.getElementById("refreshBtn2");
const cancelSelectedBtn = document.getElementById("cancelSelectedBtn");
const logoutBtn = document.getElementById("logoutBtn");
const toast = document.getElementById("toast");
const twofaStatus = document.getElementById("twofaStatus");
const twofaHint = document.getElementById("twofaHint");
const setup2faBtn = document.getElementById("setup2faBtn");

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("toast--show");
  setTimeout(() => toast.classList.remove("toast--show"), 2200);
}

function formatValue(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  return String(value);
}

function sourceLabel(source) {
  switch (source) {
    case "papi_um":
      return "PAPI UM";
    case "papi_spot":
      return "PAPI Spot/Margin";
    case "fapi_um":
      return "FAPI UM";
    case "spot":
      return "Spot";
    case "okx_swap":
      return "OKX SWAP";
    case "okx_spot":
      return "OKX SPOT";
    case "okx_margin":
      return "OKX Margin";
    case "gate_spot":
      return "Gate Spot";
    case "gate_futures":
      return "Gate Futures";
    default:
      return source;
  }
}

async function ensureLoggedIn() {
  const resp = await fetch(withBase("/api/session"));
  if (!resp.ok) {
    window.location.href = withBase("/login");
    return false;
  }
  const data = await resp.json();
  if (!data.logged_in) {
    window.location.href = withBase("/login");
    return false;
  }
  return true;
}

function syncCredentialFields() {
  const selected = credExchange.value;
  const requiresPassphrase = selected === "okx";
  apiPassphraseInput.required = requiresPassphrase;
  apiPassphraseInput.placeholder = requiresPassphrase
    ? "required for OKX"
    : "optional";
}

function updateQueryOptionVisibility() {
  const selected = queryExchange.value;
  binanceOptions.style.display = selected === "binance" ? "block" : "none";
  okxOptions.style.display = selected === "okx" ? "block" : "none";
  gateOptions.style.display = selected === "gate" ? "block" : "none";
}

function refreshAccountOptions() {
  const selectedExchange = queryExchange.value;
  const current = accountSelect.value;
  const accounts = state.credentials
    .filter((item) => item.exchange === selectedExchange)
    .map((item) => item.label);

  accountSelect.innerHTML = "";
  accounts.forEach((label) => {
    const option = document.createElement("option");
    option.value = label;
    option.textContent = label;
    accountSelect.appendChild(option);
  });

  if (!accounts.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No account";
    accountSelect.appendChild(option);
    return;
  }

  if (current && accounts.includes(current)) {
    accountSelect.value = current;
  }
}

async function fetchCredentials() {
  const resp = await fetch(withBase("/api/credentials"));
  if (resp.status === 401) {
    window.location.href = withBase("/login");
    return;
  }
  if (!resp.ok) {
    credStatus.textContent = "Failed to load credentials.";
    return;
  }

  const data = await resp.json();
  state.credentials = data;

  if (!data.length) {
    credStatus.textContent = "No credentials saved yet.";
    refreshAccountOptions();
    return;
  }

  const lines = data.map((item) => {
    const passphrase = item.has_passphrase ? " +passphrase" : "";
    return `${item.exchange} (${item.label}) - ${item.api_key_masked}${passphrase}`;
  });
  credStatus.textContent = lines.join(" | ");
  refreshAccountOptions();
}

async function fetchTwofaStatus() {
  if (!twofaStatus) {
    return;
  }
  const resp = await fetch(withBase("/api/2fa/status"));
  if (!resp.ok) {
    twofaStatus.textContent = "Unable to load 2FA status.";
    return;
  }
  const data = await resp.json();
  if (data.enabled) {
    twofaStatus.textContent = "2FA enabled";
    twofaHint.textContent = data.verified
      ? "Session verified."
      : "Please login again with TOTP.";
  } else {
    twofaStatus.textContent = "2FA not configured";
    twofaHint.textContent = "Set up Google Authenticator to protect access.";
  }
}

function updateSelectionMeta() {
  selectionMeta.textContent = `${state.selected.size} selected`;
}

function updateSelectAllToggle() {
  if (!state.orders.length) {
    selectAll.checked = false;
    selectAll.indeterminate = false;
    return;
  }
  const selectedCount = state.selected.size;
  selectAll.checked = selectedCount === state.orders.length;
  selectAll.indeterminate = selectedCount > 0 && selectedCount < state.orders.length;
}

function setCardStatus(card, status, text) {
  card.classList.remove("order-card--success", "order-card--error", "order-card--pending");
  if (status) {
    card.classList.add(`order-card--${status}`);
  }
  const overlay = card.querySelector(".order-card__overlay");
  if (overlay && text) {
    overlay.textContent = text;
  }
}

function renderOrders(orders) {
  state.orders = orders;
  state.selected.clear();
  state.cardMap.clear();
  ordersGrid.innerHTML = "";

  if (!orders.length) {
    ordersHint.textContent = "No open orders.";
    updateSelectionMeta();
    updateSelectAllToggle();
    return;
  }

  ordersHint.textContent = `${orders.length} open orders loaded.`;

  orders.forEach((order) => {
    const card = document.createElement("div");
    card.className = "order-card";
    card.dataset.orderId = order.id;

    card.innerHTML = `
      <div class="order-card__overlay">Canceled</div>
      <div class="order-card__head">
        <div class="order-card__symbol">${formatValue(order.symbol)}</div>
        <div class="order-card__chip">${formatValue(order.exchange)} / ${sourceLabel(order.source)}</div>
      </div>
      <div class="order-card__meta">
        <div>Side <span>${formatValue(order.side)}</span></div>
        <div>Type <span>${formatValue(order.order_type)}</span></div>
        <div>Price <span>${formatValue(order.price)}</span></div>
        <div>Qty <span>${formatValue(order.orig_qty)}</span></div>
        <div>Filled <span>${formatValue(order.executed_qty)}</span></div>
        <div>Order ID <span>${formatValue(order.order_id)}</span></div>
      </div>
      <div class="order-card__actions">
        <label class="check">
          <input type="checkbox" class="order-select" />
          <span>Select</span>
        </label>
        <button class="btn btn--danger btn--small" type="button">Cancel</button>
        <div class="order-card__status">${formatValue(order.status)}</div>
      </div>
    `;

    const checkbox = card.querySelector(".order-select");
    const cancelBtn = card.querySelector("button");

    checkbox.addEventListener("change", (event) => {
      if (event.target.checked) {
        state.selected.add(order.id);
      } else {
        state.selected.delete(order.id);
      }
      updateSelectionMeta();
      updateSelectAllToggle();
    });

    cancelBtn.addEventListener("click", () => {
      cancelOrders([order]);
    });

    ordersGrid.appendChild(card);
    state.cardMap.set(order.id, card);
  });

  updateSelectionMeta();
  updateSelectAllToggle();
}

function buildQueryPayload() {
  const exchange = queryExchange.value;
  const account = accountSelect.value;
  const payload = { exchange, account };

  if (exchange === "binance") {
    payload.binance = {
      account_mode: binanceMode.value,
      papi_um: document.getElementById("optPapiUm").checked,
      papi_spot: document.getElementById("optPapiSpot").checked,
      fapi_um: document.getElementById("optFapiUm").checked,
      spot: document.getElementById("optSpot").checked,
    };
  } else if (exchange === "okx") {
    payload.okx = {
      swap: document.getElementById("optOkxSwap").checked,
      spot: document.getElementById("optOkxSpot").checked,
      margin: document.getElementById("optOkxMargin").checked,
    };
  } else if (exchange === "gate") {
    payload.gate = {
      spot: document.getElementById("optGateSpot").checked,
      futures: document.getElementById("optGateFutures").checked,
      spot_account: (document.getElementById("gateSpotAccount").value || "unified").trim() || "unified",
      settle: (document.getElementById("gateSettle").value || "usdt").trim() || "usdt",
    };
  }

  return payload;
}

async function detectBinanceMode() {
  if (queryExchange.value !== "binance") {
    showToast("Switch query exchange to Binance first");
    return;
  }
  const account = accountSelect.value;
  if (!account) {
    showToast("Select a Binance account first");
    return;
  }

  const resp = await fetch(withBase("/api/binance/account_mode"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account }),
  });

  if (resp.status === 401) {
    window.location.href = withBase("/login");
    return;
  }

  if (!resp.ok) {
    const text = await resp.text();
    showToast(text || "Detect mode failed");
    return;
  }

  const data = await resp.json();
  const mode = data.mode || "UNKNOWN";
  const via = data.via || "-";
  binanceModeHint.textContent = `Mode: ${mode} (via ${via})`;

  if (mode === "UNIFIED") {
    binanceMode.value = "UNIFIED";
    document.getElementById("optPapiUm").checked = true;
    document.getElementById("optPapiSpot").checked = true;
    document.getElementById("optFapiUm").checked = true;
    document.getElementById("optSpot").checked = false;
  } else if (mode === "STANDARD") {
    binanceMode.value = "STANDARD";
    document.getElementById("optPapiUm").checked = false;
    document.getElementById("optPapiSpot").checked = false;
    document.getElementById("optFapiUm").checked = true;
    document.getElementById("optSpot").checked = true;
  }
}

async function queryOrders() {
  const payload = buildQueryPayload();
  if (!payload.account) {
    showToast("Select an account first");
    return;
  }
  state.lastQuery = payload;
  ordersHint.textContent = "Loading...";

  const resp = await fetch(withBase("/api/orders/query"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (resp.status === 401) {
    window.location.href = withBase("/login");
    return;
  }
  if (!resp.ok) {
    const text = await resp.text();
    ordersHint.textContent = "Query failed.";
    showToast(text || "Query failed");
    return;
  }

  const data = await resp.json();
  renderOrders(data.orders || []);
  if (data.errors && data.errors.length) {
    showToast(data.errors.join(" | "));
  }
  const orderCount = (data.orders || []).length;
  ordersHint.textContent = `${orderCount} open orders for ${payload.exchange}/${payload.account}`;
}

function buildCancelPayload(orderList) {
  const exchange = state.lastQuery?.exchange || queryExchange.value;
  const account = state.lastQuery?.account || accountSelect.value;
  const payload = {
    exchange,
    account,
    orders: orderList.map((order) => ({
      id: order.id,
      source: order.source,
      symbol: order.symbol,
      order_id: order.order_id,
      client_order_id: order.client_order_id,
    })),
  };

  if (exchange === "gate") {
    payload.gate = state.lastQuery?.gate || {
      spot: true,
      futures: true,
      spot_account: (document.getElementById("gateSpotAccount").value || "unified").trim() || "unified",
      settle: (document.getElementById("gateSettle").value || "usdt").trim() || "usdt",
    };
  }

  return payload;
}

async function cancelOrders(orderList) {
  if (!orderList.length) {
    return;
  }

  const account = state.lastQuery?.account || accountSelect.value;
  if (!account) {
    showToast("Select an account first");
    return;
  }

  const payload = buildCancelPayload(orderList);

  orderList.forEach((order) => {
    const card = state.cardMap.get(order.id);
    if (card) {
      setCardStatus(card, "pending", "Canceling...");
    }
  });

  const resp = await fetch(withBase("/api/orders/cancel"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (resp.status === 401) {
    window.location.href = withBase("/login");
    return;
  }
  if (!resp.ok) {
    const text = await resp.text();
    showToast(text || "Cancel failed");
    orderList.forEach((order) => {
      const card = state.cardMap.get(order.id);
      if (card) {
        setCardStatus(card, "error", "Failed");
      }
    });
    return;
  }

  const data = await resp.json();
  const results = data.results || [];
  results.forEach((result) => {
    const card = state.cardMap.get(result.id);
    if (!card) {
      return;
    }
    if (result.ok) {
      setCardStatus(card, "success", "Canceled");
    } else {
      setCardStatus(card, "error", "Failed");
    }
  });

  const failures = results.filter((item) => !item.ok);
  if (failures.length) {
    showToast(`${failures.length} cancel failed`);
  }

  setTimeout(() => {
    if (state.lastQuery) {
      queryOrders();
    }
  }, 900);
}

selectAll.addEventListener("change", (event) => {
  const checked = event.target.checked;
  state.selected.clear();
  state.orders.forEach((order) => {
    const card = state.cardMap.get(order.id);
    if (!card) {
      return;
    }
    const checkbox = card.querySelector(".order-select");
    checkbox.checked = checked;
    if (checked) {
      state.selected.add(order.id);
    }
  });
  updateSelectionMeta();
  updateSelectAllToggle();
});

queryExchange.addEventListener("change", () => {
  updateQueryOptionVisibility();
  refreshAccountOptions();
});

credExchange.addEventListener("change", syncCredentialFields);

refreshBtn.addEventListener("click", queryOrders);
refreshBtn2.addEventListener("click", queryOrders);

if (detectBinanceModeBtn) {
  detectBinanceModeBtn.addEventListener("click", detectBinanceMode);
}

if (logoutBtn) {
  logoutBtn.addEventListener("click", async () => {
    await fetch(withBase("/api/logout"), { method: "POST" });
    window.location.href = withBase("/login");
  });
}

if (setup2faBtn) {
  setup2faBtn.addEventListener("click", () => {
    window.location.href = withBase("/2fa/setup");
  });
}

cancelSelectedBtn.addEventListener("click", () => {
  const selectedOrders = state.orders.filter((order) => state.selected.has(order.id));
  if (!selectedOrders.length) {
    showToast("No orders selected");
    return;
  }
  cancelOrders(selectedOrders);
});

credForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    exchange: credExchange.value,
    label: document.getElementById("label").value,
    api_key: document.getElementById("apiKey").value,
    api_secret: document.getElementById("apiSecret").value,
    api_passphrase: (apiPassphraseInput.value || "").trim() || null,
  };

  const resp = await fetch(withBase("/api/credentials"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!resp.ok) {
    const text = await resp.text();
    showToast(text || "Failed to save credentials");
    return;
  }

  showToast("Credentials saved");
  credForm.reset();
  syncCredentialFields();
  fetchCredentials();
});

async function init() {
  const ok = await ensureLoggedIn();
  if (!ok) {
    return;
  }
  syncCredentialFields();
  updateQueryOptionVisibility();
  await fetchCredentials();
  fetchTwofaStatus();
}

init();
