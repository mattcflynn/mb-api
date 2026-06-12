// Geospatial helpers — haversine + nearby store search.

export function haversineMi(lat1, lon1, lat2, lon2) {
  const R = 3958.8;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

// Returns [{store, distMi}] sorted by distance.
export function findNearbyStores(storesData, lat, lon, radiusMi = 25) {
  const out = [];
  const latSpan = radiusMi / 69;
  const lonSpan = radiusMi / (69 * Math.cos((lat * Math.PI) / 180) || 1);
  for (const s of storesData.stores) {
    if (Math.abs(s.lat - lat) > latSpan || Math.abs(s.lon - lon) > lonSpan) continue;
    const distMi = haversineMi(lat, lon, s.lat, s.lon);
    if (distMi <= radiusMi) out.push({ store: s, distMi });
  }
  return out.sort((a, b) => a.distMi - b.distMi);
}

// Avg price (cents) for item at index `idx` across nearby stores. 0 = no data.
export function avgPriceForArea(nearby, idx) {
  let sum = 0, n = 0;
  for (const { store } of nearby) {
    const p = store.prices[idx];
    if (p > 0) { sum += p; n++; }
  }
  return n ? { avg: Math.round(sum / n), n } : { avg: 0, n: 0 };
}

// Per-store avg across all items (requires >= minItems priced). Sorted cheap → pricey.
export function storeOverallAvgs(nearby, minItems = 10) {
  const out = [];
  for (const { store, distMi } of nearby) {
    const priced = store.prices.filter((p) => p > 0);
    if (priced.length < minItems) continue;
    out.push({
      store, distMi,
      avg: Math.round(priced.reduce((a, b) => a + b, 0) / priced.length),
      itemCount: priced.length,
    });
  }
  return out.sort((a, b) => a.avg - b.avg);
}
