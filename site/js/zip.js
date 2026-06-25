import { getItems, getStores, getZipLatLon, money } from "./lib/data.js";
import { renderHeader, renderFooter, storeCell, escapeHtml } from "./lib/ui.js";
import { findNearbyStores, avgPriceForArea, storeOverallAvgs } from "./lib/geo.js";

const CATEGORY_ORDER = [
  "Tacos", "Burritos", "Cantina Chicken Menu",
  "Quesadillas", "Nachos", "Specialties",
];

function fail(msg) {
  const menu = document.getElementById("menu");
  menu.classList.remove("loading");
  menu.innerHTML = `<div class="notice">${escapeHtml(msg)}</div>`;
}

function renderMenu(items, itemCids, nearby, zip) {
  const idxOf = new Map(itemCids.map((cid, i) => [cid, i]));
  const byCat = new Map(CATEGORY_ORDER.map((c) => [c, []]));
  items.forEach((it) => {
    const { avg, n } = avgPriceForArea(nearby, idxOf.get(it.cid));
    if (!avg) return;
    if (!byCat.has(it.category)) byCat.set(it.category, []);
    byCat.get(it.category).push({ ...it, localAvg: avg, localN: n });
  });

  const menu = document.getElementById("menu");
  menu.classList.remove("loading");
  menu.innerHTML = "";
  let any = false;
  for (const [cat, catItems] of byCat) {
    if (!catItems.length) continue;
    any = true;
    catItems.sort((a, b) => a.localAvg - b.localAvg);
    const section = document.createElement("div");
    section.className = "menu-category";
    section.innerHTML = `<h2>${escapeHtml(cat)}</h2>`;
    const ul = document.createElement("ul");
    ul.className = "menu-items";
    ul.innerHTML = catItems.map((it) => {
      const delta = it.localAvg - it.national_avg_cents;
      const deltaTxt = delta === 0 ? "" :
        ` <span class="muted">(${delta > 0 ? "+" : "−"}${money(Math.abs(delta)).slice(1)} vs USA)</span>`;
      return `
      <li class="menu-item">
        <a class="item-name" href="item.html?id=${encodeURIComponent(it.cid)}&zip=${zip}">${escapeHtml(it.name)}</a>${deltaTxt}
        <span class="dots"></span>
        <span class="price">${money(it.localAvg)}</span>
      </li>`;
    }).join("");
    section.appendChild(ul);
    menu.appendChild(section);
  }
  if (!any) fail("No priced items found near this ZIP.");
}

function renderHiLo(nearby) {
  const ranked = storeOverallAvgs(nearby);
  const fill = (id, rows) => {
    document.querySelector(`#${id} tbody`).innerHTML = rows.map((r) => `
      <tr><td>${storeCell(r.store, ` <span class="muted">${r.distMi.toFixed(1)}mi</span>`)}</td>
      <td class="r">${money(r.avg)}</td></tr>`).join("");
  };
  fill("lo5", ranked.slice(0, 5));
  fill("hi5", ranked.slice(-5).reverse());
}

async function main() {
  renderHeader();
  document.getElementById("zip-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const zip = document.getElementById("zip-input").value.trim();
    if (/^\d{5}$/.test(zip)) location.href = `zip.html?zip=${zip}`;
  });

  const zip = new URLSearchParams(location.search).get("zip") || "";
  if (!/^\d{5}$/.test(zip)) return fail("Invalid ZIP. Enter a 5-digit ZIP code.");
  document.getElementById("zip-title").textContent = `Menu Near ${zip}`;

  const [itemsData, storesData, zipTable] = await Promise.all([
    getItems(), getStores(), getZipLatLon(),
  ]);
  renderFooter(itemsData.generated_at);

  const coords = zipTable[zip];
  if (!coords) return fail(`ZIP ${zip} not found. Double-check it, or try a neighboring ZIP.`);

  let radius = 25;
  let nearby = findNearbyStores(storesData, coords[0], coords[1], radius);
  if (!nearby.length) {
    radius = 50;
    nearby = findNearbyStores(storesData, coords[0], coords[1], radius);
    if (nearby.length) {
      document.getElementById("notice").innerHTML =
        `<div class="notice">No stores within 25 miles — showing stores within 50 miles.</div>`;
    }
  }
  if (!nearby.length) return fail("No Taco Bell stores with price data within 50 miles of this ZIP.");

  document.getElementById("board-sub").textContent =
    `avg over ${nearby.length} stores within ${radius} miles`;
  renderMenu(itemsData.items, storesData.item_cids, nearby, zip);
  renderHiLo(nearby);
}

main().catch((err) => fail(`ERROR: ${err.message}`));
