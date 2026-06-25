import { getItems, getStores, money } from "./lib/data.js";
import { renderHeader, renderFooter, escapeHtml } from "./lib/ui.js";

const CATEGORY_ORDER = [
  "Tacos", "Burritos", "Cantina Chicken Menu",
  "Quesadillas", "Nachos", "Specialties",
];

function fmtDate(d) {
  // d is "YYYY-MM-DD"; parse at midday so localizing never rolls back a day.
  const t = new Date(`${d}T12:00:00`);
  return isNaN(t) ? d : t.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function fail(msg) {
  const menu = document.getElementById("menu");
  menu.classList.remove("loading");
  menu.innerHTML = `<div class="notice">${escapeHtml(msg)}</div>`;
}

function renderMenu(items, itemCids, store) {
  const idxOf = new Map(itemCids.map((cid, i) => [cid, i]));
  const byCat = new Map(CATEGORY_ORDER.map((c) => [c, []]));
  items.forEach((it) => {
    const price = store.prices[idxOf.get(it.cid)];
    if (!price) return;
    if (!byCat.has(it.category)) byCat.set(it.category, []);
    byCat.get(it.category).push({ ...it, storePrice: price });
  });

  const menu = document.getElementById("menu");
  menu.classList.remove("loading");
  menu.innerHTML = "";
  let count = 0;
  for (const [cat, catItems] of byCat) {
    if (!catItems.length) continue;
    count += catItems.length;
    catItems.sort((a, b) => a.storePrice - b.storePrice);
    const section = document.createElement("div");
    section.className = "menu-category";
    section.innerHTML = `<h2>${escapeHtml(cat)}</h2>`;
    const ul = document.createElement("ul");
    ul.className = "menu-items";
    ul.innerHTML = catItems.map((it) => {
      const delta = it.storePrice - it.national_avg_cents;
      const deltaTxt = delta === 0 ? "" :
        ` <span class="muted">(${delta > 0 ? "+" : "−"}${money(Math.abs(delta)).slice(1)} vs USA)</span>`;
      return `
      <li class="menu-item">
        <a class="item-name" href="item.html?id=${encodeURIComponent(it.cid)}${store.zip ? `&zip=${store.zip}` : ""}">${escapeHtml(it.name)}</a>${deltaTxt}
        <span class="dots"></span>
        <span class="price">${money(it.storePrice)}</span>
      </li>`;
    }).join("");
    section.appendChild(ul);
    menu.appendChild(section);
  }
  return count;
}

async function main() {
  renderHeader();
  const sid = new URLSearchParams(location.search).get("sid") || "";
  if (!sid) return fail("No store specified.");
  if (!/^[A-Za-z0-9]{1,16}$/.test(sid)) return fail("Invalid store ID.");

  const [itemsData, storesData] = await Promise.all([getItems(), getStores()]);
  renderFooter(itemsData.generated_at);

  const store = storesData.stores.find((s) => String(s.sid) === sid);
  if (!store) return fail(`Store #${sid} not found (no price data).`);

  document.title = `MacroBell — Store #${sid} ${store.city}, ${store.state}`;
  document.getElementById("store-title").textContent = `${store.city}, ${store.state}`;
  document.getElementById("store-addr").textContent =
    `${store.addr} • store #${sid} • this store's prices`;
  document.getElementById("mb-link").href = `macrobell.html?sid=${encodeURIComponent(sid)}`;

  const count = renderMenu(itemsData.items, storesData.item_cids, store);

  const priced = store.prices.filter((p) => p > 0);
  const avg = priced.length
    ? Math.round(priced.reduce((a, b) => a + b, 0) / priced.length) : 0;
  document.querySelector("#store-facts tbody").innerHTML = `
    <tr><td>Store #</td><td class="r">${escapeHtml(sid)}</td></tr>
    <tr><td>Address</td><td class="r" style="font-family:Verdana;font-size:11px">${escapeHtml(store.addr)}</td></tr>
    <tr><td>City</td><td class="r" style="font-family:Verdana;font-size:11px">${escapeHtml(store.city)}, ${escapeHtml(store.state)} ${escapeHtml(store.zip)}</td></tr>
    <tr><td>Items priced</td><td class="r">${count}</td></tr>
    <tr><td>Avg item price</td><td class="r">${avg ? money(avg) : "—"}</td></tr>
    <tr><td>Prices updated</td><td class="r">${store.updated ? escapeHtml(fmtDate(store.updated)) : "—"}</td></tr>`;
}

main().catch((err) => fail(`ERROR: ${err.message}`));
