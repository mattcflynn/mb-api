// Fetch + cache site data JSON.
const cache = {};
let _storesKey = null;

export async function fetchData(name) {
  if (cache[name]) return cache[name];
  const res = await fetch(`data/${name}.json`);
  if (!res.ok) throw new Error(`fetch ${name}: ${res.status}`);
  cache[name] = await res.json();
  return cache[name];
}

export const getItems = () => fetchData("items");
export const getNational = () => fetchData("national");
export const getZipLatLon = () => fetchData("zip_latlon");
export const getHistory = (slug) => fetchData(`history/${slug}`);

// Use items.json generated_at to version the sessionStorage key so a
// data rebuild automatically invalidates any cached stores.json.
async function resolveStoresKey() {
  if (_storesKey) return _storesKey;
  const items = await fetchData("items");
  _storesKey = `mb_stores_${items.generated_at}`;
  return _storesKey;
}

// stores.json is ~2MB — persist across pages in sessionStorage.
export async function getStores() {
  if (cache.stores) return cache.stores;
  const key = await resolveStoresKey();
  const stored = sessionStorage.getItem(key);
  if (stored) {
    cache.stores = JSON.parse(stored);
    return cache.stores;
  }
  const data = await fetchData("stores");
  // Prune any stale versions before writing
  for (const k of Object.keys(sessionStorage)) {
    if (k.startsWith("mb_stores_")) sessionStorage.removeItem(k);
  }
  try { sessionStorage.setItem(key, JSON.stringify(data)); } catch (e) { /* quota */ }
  cache.stores = data;
  return data;
}

export const money = (cents) => `$${(cents / 100).toFixed(2)}`;
