import { getItems, getHistory, getStores, getZipLatLon, money } from "./lib/data.js";
import { renderHeader, renderFooter, storeCell } from "./lib/ui.js";
import { findNearbyStores } from "./lib/geo.js";
import { drawPriceChart } from "./lib/chart.js";

const NUTRI_ROWS = [
  ["calories", "Calories", ""],
  ["protein", "Protein", "g"],
  ["fat", "Total Fat", "g"],
  ["sat_fat", "Saturated Fat", "g"],
  ["trans_fat", "Trans Fat", "g"],
  ["cholesterol", "Cholesterol", "mg"],
  ["sodium", "Sodium", "mg"],
  ["carb", "Total Carbs", "g"],
  ["fiber", "Fiber", "g"],
  ["sugar", "Sugars", "g"],
];

function storeRows(stores) {
  return stores.map((s) => `
    <tr><td>${storeCell(s)}</td><td class="r">${money(s.price_cents)}</td></tr>`).join("");
}

function renderStats(it) {
  const stats = [
    ["National Avg", money(it.national_avg_cents), `${it.store_count.toLocaleString()} stores`],
    ["Lowest", money(it.national_min_cents), `${it.lo5_stores[0].city}, ${it.lo5_stores[0].state}`],
    ["Highest", money(it.national_max_cents), `${it.hi5_stores[0].city}, ${it.hi5_stores[0].state}`],
    ["Protein / $", `${it.protein_per_dollar}g`, `${it.protein}g @ avg price`],
  ];
  document.getElementById("stats").innerHTML = stats.map(([label, value, sub]) => `
    <div class="stat-box">
      <div class="label">${label}</div>
      <div class="value">${value}</div>
      <div class="sub">${sub}</div>
    </div>`).join("");
}

function renderDeviation(it) {
  const pct = Math.round(it.cv * 100);
  const ptile = it.cv_percentile;
  const phrase = ptile >= 50
    ? `varies MORE than ${ptile}% of menu items`
    : `varies LESS than ${100 - ptile}% of menu items`;
  document.getElementById("deviation").innerHTML = `
    <p style="font-size:12px;margin:6px 0">Across the country this item's price swings
    <span class="price">±${pct}%</span> around its average — it ${phrase}.</p>
    <div class="dev-bar"><div class="fill" style="width:${ptile}%"></div></div>
    <p class="muted" style="margin:2px 0">← steady pricing&nbsp;&nbsp;|&nbsp;&nbsp;wild pricing →</p>`;
}

function renderNutrition(it) {
  document.getElementById("nutrition").innerHTML = NUTRI_ROWS.map(([key, label, unit]) => `
    <tr><td>${label}</td><td>${it[key] ?? "—"}${unit}</td></tr>`).join("");
}

async function renderLocal(it, zip) {
  const [storesData, zipTable] = await Promise.all([getStores(), getZipLatLon()]);
  const coords = zipTable[zip];
  if (!coords) return;
  const nearby = findNearbyStores(storesData, coords[0], coords[1], 25)
    .concat() // already sorted by distance
    .map(({ store, distMi }) => ({
      store, distMi,
      price: store.prices[storesData.item_cids.indexOf(it.cid)],
    }))
    .filter((r) => r.price > 0)
    .sort((a, b) => a.price - b.price);
  if (!nearby.length) return;

  document.getElementById("local-zip").textContent = zip;
  document.getElementById("local-panel").style.display = "";
  const rows = (list) => list.map((r) => `
    <tr><td>${storeCell(r.store, ` <span class="muted">${r.distMi.toFixed(1)}mi</span>`)}</td>
    <td class="r">${money(r.price)}</td></tr>`).join("");
  document.querySelector("#local-lo5 tbody").innerHTML =
    rows(nearby.slice(0, 5)) +
    (nearby.length > 5 ? `<tr><td class="muted" colspan="2">…priciest near you:</td></tr>` + rows(nearby.slice(-3).reverse()) : "");
}

async function main() {
  renderHeader();
  const params = new URLSearchParams(location.search);
  const cid = params.get("id");
  const zip = params.get("zip");

  const itemsData = await getItems();
  renderFooter(itemsData.generated_at);
  const it = itemsData.items.find((i) => String(i.cid) === cid);
  if (!it) {
    document.getElementById("item-name").textContent = "Item not found";
    return;
  }

  document.title = `MacroBell — ${it.name}`;
  document.getElementById("item-name").textContent = it.name;
  document.getElementById("item-cat").textContent = it.category;
  renderStats(it);
  renderDeviation(it);
  renderNutrition(it);
  document.querySelector("#lo5 tbody").innerHTML = storeRows(it.lo5_stores);
  document.querySelector("#hi5 tbody").innerHTML = storeRows(it.hi5_stores);

  const history = await getHistory(it.slug);
  if (history.monthly.length) {
    drawPriceChart(document.getElementById("chart"), history.monthly);
  } else {
    document.getElementById("chart").innerHTML = `<p class="muted">No history yet.</p>`;
  }

  if (zip && /^\d{5}$/.test(zip)) renderLocal(it, zip).catch(() => {});
}

main().catch((err) => {
  document.getElementById("item-name").textContent = `ERROR: ${err.message}`;
});
