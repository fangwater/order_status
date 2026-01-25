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

const lookupForm = document.getElementById("lookupForm");
const accountSelect = document.getElementById("accountSelect");
const sourceSelect = document.getElementById("sourceSelect");
const symbolInput = document.getElementById("symbol");
const orderIdInput = document.getElementById("orderId");
const clientOrderIdInput = document.getElementById("clientOrderId");
const resultGrid = document.getElementById("resultGrid");
const resultHint = document.getElementById("resultHint");
const lookupHint = document.getElementById("lookupHint");
const logoutBtn = document.getElementById("logoutBtn");
const toast = document.getElementById("toast");

function showToast(message) {
  if (!toast) {
    return;
  }
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

function formatTimestamp(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  const ms = Number(value);
  if (!Number.isFinite(ms)) {
    return "-";
  }
  const utc = formatUtc(ms);
  const shanghai = formatShanghai(ms);
  return `${utc} UTC / ${shanghai} GMT+8`;
}

function formatUtc(ms) {
  const date = new Date(ms);
  return formatDateParts(
    date.getUTCFullYear(),
    date.getUTCMonth() + 1,
    date.getUTCDate(),
    date.getUTCHours(),
    date.getUTCMinutes(),
    date.getUTCSeconds()
  );
}

function formatShanghai(ms) {
  const date = new Date(ms + 8 * 60 * 60 * 1000);
  return formatDateParts(
    date.getUTCFullYear(),
    date.getUTCMonth() + 1,
    date.getUTCDate(),
    date.getUTCHours(),
    date.getUTCMinutes(),
    date.getUTCSeconds()
  );
}

function formatDateParts(year, month, day, hour, minute, second) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${year}-${pad(month)}-${pad(day)} ${pad(hour)}:${pad(minute)}:${pad(second)}`;
}

function sourceLabel(source) {
  switch (source) {
    case "papi_um":
      return "PAPI UM";
    case "papi_spot":
      return "PAPI Margin";
    case "fapi_um":
      return "FAPI UM";
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

async function fetchCredentials() {
  const resp = await fetch(withBase("/api/credentials"));
  if (resp.status === 401) {
    window.location.href = withBase("/login");
    return;
  }
  if (!resp.ok) {
    lookupHint.textContent = "Failed to load credentials.";
    return;
  }
  const data = await resp.json();
  if (!data.length) {
    accountSelect.innerHTML = "<option value=\"\">No account</option>";
    lookupHint.textContent = "No credentials saved yet.";
    return;
  }
  const accounts = data
    .filter((item) => item.exchange === "binance")
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
  }
}

function renderOrders(orders) {
  resultGrid.innerHTML = "";
  if (!orders.length) {
    resultHint.textContent = "No order found.";
    return;
  }
  resultHint.textContent = `${orders.length} order loaded.`;
  orders.forEach((order) => {
    const card = document.createElement("div");
    card.className = "order-card";
    card.innerHTML = `
      <div class="order-card__head">
        <div class="order-card__symbol">${formatValue(order.symbol)}</div>
        <div class="order-card__chip">${sourceLabel(order.source)}</div>
      </div>
      <div class="order-card__meta">
        <div>Side <span>${formatValue(order.side)}</span></div>
        <div>Type <span>${formatValue(order.order_type)}</span></div>
        <div>Status <span>${formatValue(order.status)}</span></div>
        <div>Price <span>${formatValue(order.price)}</span></div>
        <div>Qty <span>${formatValue(order.orig_qty)}</span></div>
        <div>Filled <span>${formatValue(order.executed_qty)}</span></div>
        <div>Order ID <span>${formatValue(order.order_id)}</span></div>
        <div>Client ID <span>${formatValue(order.client_order_id)}</span></div>
        <div>Position <span>${formatValue(order.position_side)}</span></div>
        <div>Reduce Only <span>${formatValue(order.reduce_only)}</span></div>
        <div>Time <span>${formatTimestamp(order.time)}</span></div>
        <div>Updated <span>${formatTimestamp(order.update_time)}</span></div>
      </div>
    `;
    resultGrid.appendChild(card);
  });
}

async function lookupOrder(event) {
  event.preventDefault();
  const account = accountSelect.value;
  if (!account) {
    showToast("Select an account first");
    return;
  }
  const symbol = symbolInput.value.trim().toUpperCase();
  if (!symbol) {
    showToast("Symbol is required");
    return;
  }
  const orderId = orderIdInput.value.trim();
  const clientOrderId = clientOrderIdInput.value.trim();
  if (!orderId && !clientOrderId) {
    showToast("Provide order ID or client order ID");
    return;
  }

  resultHint.textContent = "Loading...";

  const payload = {
    exchange: document.getElementById("exchange").value,
    account,
    source: sourceSelect.value,
    symbol,
    order_id: orderId || null,
    client_order_id: clientOrderId || null,
  };

  const resp = await fetch(withBase("/api/orders/lookup"), {
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
    resultHint.textContent = "Query failed.";
    showToast(text || "Query failed");
    return;
  }

  const data = await resp.json();
  renderOrders(data.orders || []);
  if (data.errors && data.errors.length) {
    showToast(data.errors.join(" | "));
  }
}

async function init() {
  const ok = await ensureLoggedIn();
  if (!ok) {
    return;
  }
  fetchCredentials();
}

lookupForm.addEventListener("submit", lookupOrder);
if (logoutBtn) {
  logoutBtn.addEventListener("click", async () => {
    await fetch(withBase("/api/logout"), { method: "POST" });
    window.location.href = withBase("/login");
  });
}

init();
