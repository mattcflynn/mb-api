import { getItems, money } from "./lib/data.js";
import { renderHeader, renderFooter } from "./lib/ui.js";

const COLS = [
  { key: "name", label: "Item", text: true },
  { key: "category", label: "Category", text: true },
  { key: "national_avg_cents", label: "Avg $", fmt: money },
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

function render() {
  const visible = applyFilters().sort((a, b) => {
    const av = a[sortKey], bv = b[sortKey];
    const cmp = typeof av === "string" ? av.localeCompare(bv) : av - bv;
    return sortAsc ? cmp : -cmp;
  });

  document.getElementById("head-row").innerHTML = COLS.map((c) => `
    <th data-key="${c.key}" class="${c.key === sortKey ? "sorted" : ""}">
      ${c.label}${c.key === sortKey ? (sortAsc ? " ▲" : " ▼") : ""}</th>`).join("");

  document.getElementById("body").innerHTML = visible.map((r) => `
    <tr>${COLS.map((c) => {
      const v = r[c.key];
      const txt = c.text ? v : (c.fmt ? c.fmt(v) : v);
      const cell = c.key === "name"
        ? `<a class="item-name" href="item.html?id=${encodeURIComponent(r.cid)}">${v}</a>`
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

async function main() {
  renderHeader("macrobell.html");
  const itemsData = await getItems();
  renderFooter(itemsData.generated_at);
  rows = itemsData.items.map((it) => ({
    ...it,
    cal_per_dollar: it.calories / (it.national_avg_cents / 100),
  }));
  buildControls();
  render();
}

main().catch((err) => {
  document.getElementById("body").innerHTML = `<tr><td colspan="12">ERROR: ${err.message}</td></tr>`;
});
