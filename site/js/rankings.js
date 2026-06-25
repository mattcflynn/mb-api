import { getItems, getStores, getZipLatLon, money } from "./lib/data.js";
import { renderHeader, renderFooter, storeCell, escapeHtml } from "./lib/ui.js";
import { findNearbyStores } from "./lib/geo.js";

const PAGE_SIZE = 25;
const MIN_ITEMS = 30;

let allRanked = [];  // computed once from full stores list
let filtered = [];  // current view after location + sort
let shown = 0;
let storesData = null;

// --- Compute per-store average across all priced items ---
function computeRanked(stores) {
  const out = [];
  for (const store of stores) {
    const prices = store.prices.filter(p => p > 0);
    if (prices.length < MIN_ITEMS) continue;
    const avg = Math.round(prices.reduce((a, b) => a + b, 0) / prices.length);
    out.push({ store, avg, itemCount: prices.length });
  }
  return out;
}

function sorted(arr) {
  const asc = document.getElementById("sort-mode").value === "cheap";
  return [...arr].sort((a, b) => asc ? a.avg - b.avg : b.avg - a.avg);
}

// --- Rendering ---
function renderRows() {
  const tbody = document.getElementById("rank-body");
  const slice = filtered.slice(shown, shown + PAGE_SIZE);
  tbody.insertAdjacentHTML("beforeend", slice.map((r, i) => `
    <tr>
      <td style="text-align:right;padding-right:10px;color:var(--tb-teal)">${shown + i + 1}</td>
      <td>${storeCell(r.store)}</td>
      <td class="r">${money(r.avg)}</td>
      <td class="muted" style="text-align:right">${r.itemCount}</td>
    </tr>`).join(""));
  shown += slice.length;
  document.getElementById("rank-status").textContent =
    `Showing ${shown.toLocaleString()} of ${filtered.length.toLocaleString()} stores`;
  document.getElementById("load-more").style.display =
    shown < filtered.length ? "" : "none";
}

function resetTable(newFiltered, subtitle) {
  filtered = newFiltered;
  shown = 0;
  document.getElementById("rank-body").innerHTML = "";
  document.getElementById("rank-sub").textContent = subtitle;
  renderRows();
}

// --- Build city autocomplete + state dropdown from store data ---
function buildLocationLists(stores) {
  const cities = [...new Set(stores.map(s => `${s.city}, ${s.state}`))].sort();
  document.getElementById("city-datalist").innerHTML =
    cities.map(c => `<option value="${escapeHtml(c)}">`).join("");

  const states = [...new Set(stores.map(s => s.state))].filter(Boolean).sort();
  const sel = document.getElementById("loc-state");
  sel.innerHTML = `<option value="">— pick a state —</option>` +
    states.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
}

// --- Location modes ---
async function applyUSA() {
  const r = sorted(allRanked);
  const label = document.getElementById("sort-mode").value === "cheap" ? "cheapest" : "priciest";
  resetTable(r, `${r.length.toLocaleString()} stores nationwide — ${label} first`);
}

async function applyZip() {
  const zip = document.getElementById("loc-zip").value.trim();
  if (!/^\d{5}$/.test(zip)) return;
  const zipTable = await getZipLatLon();
  const coords = zipTable[zip];
  if (!coords) {
    document.getElementById("rank-sub").textContent = `ZIP ${zip} not found`;
    return;
  }
  let nearby = findNearbyStores(storesData, coords[0], coords[1], 25);
  const radius = nearby.length ? 25 : 50;
  if (!nearby.length) nearby = findNearbyStores(storesData, coords[0], coords[1], 50);
  const distMap = new Map(nearby.map(({ store, distMi }) => [store.sid, distMi]));
  const local = allRanked
    .filter(r => distMap.has(r.store.sid))
    .map(r => ({ ...r, distMi: distMap.get(r.store.sid) }));
  const label = document.getElementById("sort-mode").value === "cheap" ? "cheapest" : "priciest";
  resetTable(sorted(local), `${local.length} stores within ${radius}mi of ${zip} — ${label} first`);
}

async function applyState() {
  const state = document.getElementById("loc-state").value;
  if (!state) return;
  const local = allRanked.filter(r => r.store.state === state);
  const label = document.getElementById("sort-mode").value === "cheap" ? "cheapest" : "priciest";
  resetTable(sorted(local), `${local.length} stores in ${state} — ${label} first`);
}

async function applyCity() {
  const raw = document.getElementById("loc-city").value.trim();
  if (!raw) return;
  const parts = raw.split(",").map(s => s.trim().toLowerCase());
  const cityQ = parts[0];
  const stateQ = parts[1] || "";
  const local = allRanked.filter(r => {
    const cityMatch = r.store.city.toLowerCase() === cityQ;
    const stateMatch = !stateQ || r.store.state.toLowerCase() === stateQ;
    return cityMatch && stateMatch;
  });
  const label = document.getElementById("sort-mode").value === "cheap" ? "cheapest" : "priciest";
  resetTable(sorted(local),
    `${local.length} stores in ${raw} — ${label} first`);
}

async function applyLocation() {
  const mode = document.getElementById("loc-mode").value;
  if (mode === "zip") await applyZip();
  else if (mode === "city") await applyCity();
  else if (mode === "state") await applyState();
  else await applyUSA();
}

// --- Main ---
async function main() {
  renderHeader("rankings.html");

  const params = new URLSearchParams(location.search);
  document.getElementById("sort-mode").value = params.get("sort") === "pricey" ? "pricey" : "cheap";
  document.getElementById("rank-title").textContent =
    params.get("sort") === "pricey" ? "Priciest Stores" : "Cheapest Stores";

  const [itemsData, sd] = await Promise.all([getItems(), getStores()]);
  storesData = sd;
  renderFooter(itemsData.generated_at);

  allRanked = computeRanked(storesData.stores);
  buildLocationLists(storesData.stores);

  // Wire controls
  document.getElementById("loc-mode").addEventListener("change", e => {
    document.getElementById("state-ctl").style.display = e.target.value === "state" ? "" : "none";
    document.getElementById("zip-ctl").style.display = e.target.value === "zip" ? "" : "none";
    document.getElementById("city-ctl").style.display = e.target.value === "city" ? "" : "none";
    if (e.target.value === "usa") applyUSA();
  });
  document.getElementById("loc-state").addEventListener("change", applyState);
  document.getElementById("sort-mode").addEventListener("change", applyLocation);
  document.getElementById("loc-zip-go").addEventListener("click", applyZip);
  document.getElementById("loc-zip").addEventListener("keydown", e => { if (e.key === "Enter") applyZip(); });
  document.getElementById("loc-city-go").addEventListener("click", applyCity);
  document.getElementById("loc-city").addEventListener("keydown", e => { if (e.key === "Enter") applyCity(); });
  document.getElementById("load-more").addEventListener("click", renderRows);

  await applyUSA();
}

main().catch(err => {
  document.getElementById("rank-body").innerHTML =
    `<tr><td colspan="4">ERROR: ${escapeHtml(err.message)}</td></tr>`;
});
