// Shared helpers for every page.
const fmt = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" });
const rupees = (paise) => fmt.format(paise / 100);

// Escape anything user-controlled before it goes into innerHTML.
const esc = (v) => String(v ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await res.json().catch(() => ({}));
  // Session expired on a protected call: back to sign-in. A failed sign-in stays put.
  if (res.status === 401 && path !== "/api/login") { window.location = "/"; throw new Error("unauthorised"); }
  if (!res.ok) throw new Error(body.error || (body.detail && "Please check the form") || "Something went wrong");
  return body;
}

function header() {
  document.getElementById("logout")?.addEventListener("click", async () => {
    await api("/api/logout", { method: "POST" });
    window.location = "/";
  });
}

// Client-side amount check, mirrors parse_amount() on the server.
function amountError(text) {
  const s = text.trim();
  if (!/^\d+(\.\d{1,2})?$/.test(s)) return "Enter an amount like 1500 or 1500.50";
  if (Number(s) <= 0) return "Amount must be more than zero";
  if (Number(s) > 1000000) return "Amount is above the INR 10,00,000 per-transfer limit";
  return "";
}
