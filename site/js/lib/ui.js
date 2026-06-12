// Shared header/footer/nav chrome.

const BELL_SVG = `
<svg class="logo-bell" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg" shape-rendering="crispEdges" aria-label="pixel bell">
  <rect x="7" y="1" width="2" height="1" fill="#ffd700"/>
  <rect x="6" y="2" width="4" height="1" fill="#ff69b4"/>
  <rect x="5" y="3" width="6" height="2" fill="#ff69b4"/>
  <rect x="4" y="5" width="8" height="3" fill="#6b2d8b"/>
  <rect x="3" y="8" width="10" height="3" fill="#6b2d8b"/>
  <rect x="2" y="11" width="12" height="1" fill="#00a896"/>
  <rect x="1" y="12" width="14" height="1" fill="#00a896"/>
  <rect x="6" y="13" width="4" height="2" fill="#ffd700"/>
</svg>`;

export function renderHeader(active) {
  const el = document.getElementById("header");
  if (!el) return;
  const nav = [
    ["index.html", "Menu Board"],
    ["macrobell.html", "MacroBell Tool"],
  ].map(([href, label]) =>
    `<a href="${href}"${active === href ? ' style="color:var(--tb-pink)"' : ""}>${label}</a>`
  ).join(" • ");
  el.innerHTML = `
    <header class="site-header">
      <a href="index.html" style="text-decoration:none">${BELL_SVG}
        <h1 class="wordmark">MacroBell</h1>
      </a>
      <p class="tagline">Drive-Thru Price Intelligence • Est. 2026 • Tastes Like 1994</p>
      <nav class="site-nav">${nav}</nav>
      <div class="marquee"><span>★ NATIONAL AVERAGE PRICES UPDATED WEEKLY ★ ENTER YOUR ZIP FOR LOCAL DEALS ★
      SORT THE WHOLE MENU BY PROTEIN-PER-DOLLAR WITH THE MACROBELL TOOL ★ PRICES SCRAPED FROM ${"7,500+"} STORES COAST TO COAST ★</span></div>
    </header>`;
}

export function renderFooter(generatedAt) {
  const el = document.getElementById("footer");
  if (!el) return;
  const when = generatedAt ? new Date(generatedAt).toLocaleDateString() : "";
  el.innerHTML = `
    <footer class="site-footer">
      MACROBELL is a fan-made price tracker. Not affiliated with Taco Bell Corp.<br>
      Nutrition via Nutritionix • Prices via tacobell.com store menus${when ? ` • Data built ${when}` : ""}<br>
      Best viewed in Netscape Navigator 3.0 at 800×600
    </footer>`;
}

export function el(tag, attrs = {}, html = "") {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  if (html) node.innerHTML = html;
  return node;
}
