import { getItems, getStores, getZipLatLon, money } from "./lib/data.js";
import { renderHeader, renderFooter } from "./lib/ui.js";
import { findNearbyStores } from "./lib/geo.js";

const COLS = [
  { key: "name", label: "Item", text: true },
  { key: "category", label: "Category", text: true },
  { key: "price_cents", label: "Price", fmt: money },
  { key: "protein_per_dollar", label: "Protein/$", fmt: (v) => `${v.toFixed(2)}g`, hot: true, filter: true },
  { key: "cal_per_dollar", label: "Cal/$", fmt: (v) => v.toFixed(0), filter: true },
  { key: "protein", label: "Protein", fmt: (v) => `${v}g`, filter: true },
  { key: "calories", label: "Cal", fmt: (v) => v, filter: true },
  { key: "fat", label: "Fat", fmt: (v) => `${v}g`, filter: true },
  { key: "carb", label: "Carbs", fmt: (v) => `${v}g`, filter: true },
  { key: "sodium", label: "Sodium", fmt: (v) => `${v}mg`, filter: true },
  { key: "fiber", label: "Fiber", fmt: (v) => `${v}g`, filter: true },
  { key: "cv", label: "Var%", fmt: (v) => `${Math.round(v * 100)}%` },
];

let rows = [];
let storeSid = null;
let sortKey = "protein_per_dollar";
let sortAsc = false;
const filters = {}; // key -> {min, max}
let categoryFilter = "";

function applyFilters() {
  return rows.filter((r) => {
    if (categoryFilter && r.category !== categoryFilter) return false;
    for (const [key, { min, max }] of Object.entries(filters)) {
      const v = r[key];
      if (min !== null && v < min) return false;
      if (max !== null && v > max) return false;
    }
    return true;
  });
}

function itemLink(r) {
  return `item.html?id=${encodeURIComponent(r.cid)}${storeSid ? `&zip=${r.storeZip || ""}` : ""}`;
}

function render() {
  const visible = applyFilters().sort((a, b) => {
    const av = a[sortKey], bv = b[sortKey];
    const cmp = typeof av === "string" ? av.localeCompare(bv) : av - bv;
    return sortAsc ? cmp : -cmp;
  });

  document.getElementById("head-row").innerHTML = COLS.map((c) => `
    <th data-key="${c.key}" class="${c.key === sortKey ? "sorted" : ""}">
      ${c.key === "price_cents" ? (storeSid ? "Store $" : "Avg $") : c.label}${c.key === sortKey ? (sortAsc ? " ▲" : " ▼") : ""}</th>`).join("");

  document.getElementById("body").innerHTML = visible.map((r) => `
    <tr>${COLS.map((c) => {
      const v = r[c.key];
      const txt = c.text ? v : (c.fmt ? c.fmt(v) : v);
      const cell = c.key === "name"
        ? `<a class="item-name" href="${itemLink(r)}">${v}</a>`
        : txt;
      return `<td${c.hot ? ' class="hot"' : ""}>${cell}</td>`;
    }).join("")}</tr>`).join("");

  document.getElementById("row-count").textContent =
    `${visible.length} of ${rows.length} items shown`;

  document.querySelectorAll("#head-row th").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (sortKey === key) sortAsc = !sortAsc;
      else { sortKey = key; sortAsc = COLS.find((c) => c.key === key).text === true; }
      render();
    });
  });
}

function buildControls() {
  const ctl = document.getElementById("controls");
  const cats = [...new Set(rows.map((r) => r.category))].sort();

  let html = `
    <div class="ctl">
      <label>Category</label>
      <select id="cat-filter">
        <option value="">All</option>
        ${cats.map((c) => `<option>${c}</option>`).join("")}
      </select>
    </div>`;

  for (const c of COLS.filter((c) => c.filter)) {
    html += `
      <div class="ctl">
        <label>${c.label}</label>
        <input type="number" data-key="${c.key}" data-kind="min" placeholder="min">
        <input type="number" data-key="${c.key}" data-kind="max" placeholder="max">
      </div>`;
  }
  ctl.innerHTML = html;

  document.getElementById("cat-filter").addEventListener("change", (e) => {
    categoryFilter = e.target.value;
    render();
  });
  ctl.querySelectorAll("input[type=number]").forEach((input) => {
    input.addEventListener("input", () => {
      const key = input.dataset.key;
      if (!filters[key]) filters[key] = { min: null, max: null };
      filters[key][input.dataset.kind] = input.value === "" ? null : Number(input.value);
      render();
    });
  });
}

function buildStorePicker() {
  const go = async () => {
    const zip = document.getElementById("picker-zip").value.trim();
    if (!/^\d{5}$/.test(zip)) return;
    const [storesData, zipTable] = await Promise.all([getStores(), getZipLatLon()]);
    const coords = zipTable[zip];
    const sel = document.getElementById("picker-select");
    const wrap = document.getElementById("picker-stores");
    if (!coords) {
      wrap.style.display = "";
      sel.innerHTML = `<option value="">ZIP not found</option>`;
      return;
    }
    let nearby = findNearbyStores(storesData, coords[0], coords[1], 25);
    if (!nearby.length) nearby = findNearbyStores(storesData, coords[0], coords[1], 50);
    wrap.style.display = "";
    sel.innerHTML = nearby.length
      ? `<option value="">— pick a store (${nearby.length} found) —</option>` +
        nearby.slice(0, 25).map(({ store, distMi }) =>
          `<option value="${store.sid}">${store.city}, ${store.state} — ${store.addr} (${distMi.toFixed(1)}mi)</option>`
        ).join("")
      : `<option value="">No stores within 50mi</option>`;
  };
  document.getElementById("picker-go").addEventListener("click", go);
  document.getElementById("picker-zip").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); go(); }
  });
  document.getElementById("picker-select").addEventListener("change", (e) => {
    if (e.target.value) location.href = `macrobell.html?sid=${encodeURIComponent(e.target.value)}`;
  });
}

async function main() {
  renderHeader("macrobell.html");
  storeSid = new URLSearchParams(location.search).get("sid");
  const itemsData = await getItems();
  renderFooter(itemsData.generated_at);

  if (storeSid) {
    const storesData = await getStores();
    const store = storesData.stores.find((s) => String(s.sid) === storeSid);
    if (store) {
      const idxOf = new Map(storesData.item_cids.map((cid, i) => [cid, i]));
      rows = itemsData.items
        .map((it) => ({ ...it, storePrice: store.prices[idxOf.get(it.cid)] }))
        .filter((it) => it.storePrice > 0)
        .map((it) => ({
          ...it,
          price_cents: it.storePrice,
          storeZip: store.zip,
          protein_per_dollar: it.protein / (it.storePrice / 100),
          cal_per_dollar: it.calories / (it.storePrice / 100),
        }));
      document.getElementById("mb-title").textContent = "MacroBell Analyzer — Single Store";
      document.getElementById("mb-sub").textContent = "this store's prices — click any column header to sort";
      document.getElementById("store-banner").innerHTML = `
        <div class="notice">📍 <a class="item-name" href="store.html?sid=${encodeURIComponent(storeSid)}">
        ${store.city}, ${store.state} — ${store.addr}</a> (store #${storeSid})
        &nbsp;•&nbsp; <a href="macrobell.html">switch to national averages</a></div>`;
    } else {
      document.getElementById("store-banner").innerHTML =
        `<div class="notice">Store #${storeSid} not found — showing national averages.</div>`;
      storeSid = null;
    }
  }

  if (!storeSid) {
    rows = itemsData.items.map((it) => ({
      ...it,
      price_cents: it.national_avg_cents,
      cal_per_dollar: it.calories / (it.national_avg_cents / 100),
    }));
  }

  buildStorePicker();
  buildControls();
  render();
}

main().catch((err) => {
  document.getElementById("body").innerHTML = `<tr><td colspan="12">ERROR: ${err.message}</td></tr>`;
});
