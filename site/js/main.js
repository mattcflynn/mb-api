import { getItems, getNational, money } from "./lib/data.js";
import { renderHeader, renderFooter } from "./lib/ui.js";

const CATEGORY_ORDER = [
  "Tacos", "Burritos", "Cantina Chicken Menu",
  "Quesadillas", "Nachos", "Specialties",
];

function renderMenu(items) {
  const byCat = new Map(CATEGORY_ORDER.map((c) => [c, []]));
  items.forEach((it) => {
    if (!byCat.has(it.category)) byCat.set(it.category, []);
    byCat.get(it.category).push(it);
  });

  const menu = document.getElementById("menu");
  menu.classList.remove("loading");
  menu.innerHTML = "";
  for (const [cat, catItems] of byCat) {
    if (!catItems.length) continue;
    catItems.sort((a, b) => a.national_avg_cents - b.national_avg_cents);
    const section = document.createElement("div");
    section.className = "menu-category";
    section.innerHTML = `<h2>${cat}</h2>`;
    const ul = document.createElement("ul");
    ul.className = "menu-items";
    ul.innerHTML = catItems.map((it) => `
      <li class="menu-item">
        <a class="item-name" href="item.html?id=${encodeURIComponent(it.cid)}">${it.name}</a>
        <span class="dots"></span>
        <span class="price">${money(it.national_avg_cents)}</span>
      </li>`).join("");
    section.appendChild(ul);
    menu.appendChild(section);
  }
}

function renderHiLo(national) {
  const fill = (id, rows) => {
    document.querySelector(`#${id} tbody`).innerHTML = rows.map((s) => `
      <tr><td>${s.city}, ${s.state}</td><td class="r">${money(s.avg_price_cents)}</td></tr>`).join("");
  };
  fill("lo5", national.lo5_overall);
  fill("hi5", national.hi5_overall);
}

async function main() {
  renderHeader("index.html");

  document.getElementById("zip-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const zip = document.getElementById("zip-input").value.trim();
    if (/^\d{5}$/.test(zip)) location.href = `zip.html?zip=${zip}`;
  });

  const [itemsData, national] = await Promise.all([getItems(), getNational()]);
  renderMenu(itemsData.items);
  renderHiLo(national);
  document.getElementById("board-sub").textContent =
    `national average prices • ${national.store_count.toLocaleString()} stores`;
  renderFooter(itemsData.generated_at);
}

main().catch((err) => {
  document.getElementById("menu").textContent = `ERROR: ${err.message}`;
});
