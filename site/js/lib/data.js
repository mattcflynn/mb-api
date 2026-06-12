// Fetch + cache site data JSON.
const cache = {};

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

// stores.json is ~2MB — persist across pages in sessionStorage.
export async function getStores() {
  if (cache.stores) return cache.stores;
  const stored = sessionStorage.getItem("mb_stores");
  if (stored) {
    cache.stores = JSON.parse(stored);
    return cache.stores;
  }
  const data = await fetchData("stores");
  try {
    sessionStorage.setItem("mb_stores", JSON.stringify(data));
  } catch (e) { /* quota — fine, fetch again next page */ }
  return data;
}

export const money = (cents) => `$${(cents / 100).toFixed(2)}`;
