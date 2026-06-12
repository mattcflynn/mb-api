// Hand-rolled 90s-style SVG bar chart for monthly price history.

const NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs) {
  const node = document.createElementNS(NS, tag);
  Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  return node;
}

function fmtMonth(ym) {
  const [y, m] = ym.split("-");
  const names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${names[+m - 1]} '${y.slice(2)}`;
}

export function drawPriceChart(container, monthly) {
  const W = 480, H = 220;
  const pad = { l: 52, r: 12, t: 16, b: 34 };
  const plotW = W - pad.l - pad.r;
  const plotH = H - pad.t - pad.b;

  const svg = svgEl("svg", {
    viewBox: `0 0 ${W} ${H}`,
    "shape-rendering": "crispEdges",
    role: "img",
    "aria-label": "monthly average price chart",
  });

  const vals = monthly.map((m) => m.avg_cents);
  const lo = Math.floor((Math.min(...vals) * 0.95) / 25) * 25;
  const hi = Math.ceil((Math.max(...vals) * 1.05) / 25) * 25;
  const y = (cents) => pad.t + plotH - ((cents - lo) / (hi - lo || 1)) * plotH;

  // frame + gridlines
  svg.appendChild(svgEl("rect", {
    x: pad.l, y: pad.t, width: plotW, height: plotH,
    fill: "#0a0a0a", stroke: "#6b2d8b", "stroke-width": 2,
  }));
  for (let i = 0; i <= 4; i++) {
    const cents = lo + ((hi - lo) * i) / 4;
    const gy = Math.round(y(cents));
    svg.appendChild(svgEl("line", {
      x1: pad.l, x2: pad.l + plotW, y1: gy, y2: gy,
      stroke: "#6b2d8b", "stroke-width": 1, "stroke-dasharray": "3,4",
    }));
    const label = svgEl("text", {
      x: pad.l - 6, y: gy + 4, "text-anchor": "end",
      fill: "#00a896", "font-family": "Courier New, monospace", "font-size": 11,
    });
    label.textContent = `$${(cents / 100).toFixed(2)}`;
    svg.appendChild(label);
  }

  // bars
  const slot = plotW / monthly.length;
  const barW = Math.max(Math.floor(slot * 0.55), 6);
  monthly.forEach((m, i) => {
    const bx = Math.round(pad.l + slot * i + (slot - barW) / 2);
    const by = Math.round(y(m.avg_cents));
    const bar = svgEl("rect", {
      x: bx, y: by, width: barW, height: pad.t + plotH - by,
      fill: "#00a896", stroke: "#ff69b4", "stroke-width": 2,
    });
    bar.addEventListener("mouseenter", (e) => showTip(container, e, m));
    bar.addEventListener("mouseleave", () => hideTip(container));
    svg.appendChild(bar);

    const xl = svgEl("text", {
      x: bx + barW / 2, y: H - 14, "text-anchor": "middle",
      fill: "#f5f5dc", "font-family": "Courier New, monospace", "font-size": 11,
    });
    xl.textContent = fmtMonth(m.ym);
    svg.appendChild(xl);

    const cov = svgEl("text", {
      x: bx + barW / 2, y: H - 3, "text-anchor": "middle",
      fill: "#f5f5dc", opacity: 0.45, "font-family": "Courier New, monospace", "font-size": 8,
    });
    cov.textContent = `${m.store_count.toLocaleString()} st`;
    svg.appendChild(cov);
  });

  container.style.position = "relative";
  container.innerHTML = "";
  container.appendChild(svg);
}

function showTip(container, e, m) {
  hideTip(container);
  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.textContent = `${fmtMonth(m.ym)}: $${(m.avg_cents / 100).toFixed(2)} (${m.store_count.toLocaleString()} stores)`;
  container.appendChild(tip);
  const rect = container.getBoundingClientRect();
  const tRect = e.target.getBoundingClientRect();
  tip.style.left = `${Math.min(tRect.left - rect.left, rect.width - 180)}px`;
  tip.style.top = `${tRect.top - rect.top - 30}px`;
}

function hideTip(container) {
  container.querySelectorAll(".chart-tip").forEach((t) => t.remove());
}
