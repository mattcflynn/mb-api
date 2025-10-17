# Coder's Note:
# This is the definitive, refactored scraper. It uses the robust product_code
# from the API and correctly parses the nested price object from the JSON.
# Last checked: October 16, 2025

import requests
import sqlite3
import time
from datetime import datetime

# --- Configuration ---
DB_FILENAME = "macrobell.db"
STAGGER_MODE = True
TODAYS_CHUNK = 1
TOTAL_CHUNKS = 3
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
}

def get_stores_to_scrape(conn):
    """
    Fetches the list of store IDs to be scraped from the database.
    Applies staggering logic if enabled.
    """
    cur = conn.cursor()
    cur.execute("SELECT store_id FROM stores ORDER BY store_id")
    all_store_ids = [row[0] for row in cur.fetchall()]
    
    if not STAGGER_MODE:
        print(f"Loaded {len(all_store_ids)} stores from the database.")
        return all_store_ids

    total_stores = len(all_store_ids)
    chunk_size = (total_stores + TOTAL_CHUNKS - 1) // TOTAL_CHUNKS
    start_index = (TODAYS_CHUNK - 1) * chunk_size
    end_index = start_index + chunk_size
    
    staggered_list = all_store_ids[start_index:end_index]
    print(f"STAGGER MODE: Loaded {len(staggered_list)} of {total_stores} stores for chunk {TODAYS_CHUNK}/{TOTAL_CHUNKS}.")
    return staggered_list

def get_latest_price(conn, store_id, product_code):
    """
    Gets the most recently recorded price for a product_code at a specific store.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT price FROM prices
        WHERE store_id = ? AND product_code = ?
        ORDER BY scrape_date DESC
        LIMIT 1
    """, (store_id, product_code))
    result = cur.fetchone()
    return result[0] if result else None

def main():
    """
    Scrapes Taco Bell API for prices and updates the database.
    """
    conn = sqlite3.connect(DB_FILENAME)
    stores_to_scrape = get_stores_to_scrape(conn)
    
    if not stores_to_scrape:
        print("No stores found in the database to scrape.")
        conn.close()
        return

    today_str = datetime.now().strftime("%Y-%m-%d")

    for i, store_id in enumerate(stores_to_scrape):
        print("\n" + "="*40)
        print(f"Processing store {i+1}/{len(stores_to_scrape)}: ID {store_id}")
        
        api_url = f"https://www.tacobell.com/tacobellwebservices/v4/tacobell/products/menu/{store_id}"
        
        try:
            response = requests.get(api_url, headers=HEADERS, timeout=15)
            response.raise_for_status()
            menu_data = response.json()

            items_processed = 0
            prices_changed = 0

            for category in menu_data.get('menuProductCategories', []):
                for product in category.get('products', []):
                    items_processed += 1
                    product_code = product.get('code')
                    price_object = product.get('price') # Get the whole price object

                    if not product_code or price_object is None:
                        continue

                    # --- THE FIX: Extract the numeric value from the price object ---
                    price = price_object.get('value')
                    if price is None:
                        continue # Skip if there's no numeric value

                    last_price = get_latest_price(conn, store_id, product_code)

                    if last_price is None or abs(last_price - price) > 0.001:
                        item_name = product.get('name', 'Unknown')
                        print(f"   -> PRICE CHANGE: '{item_name}' ({product_code}) from ${last_price} to ${price}")
                        conn.execute(
                            "INSERT INTO prices (store_id, product_code, price, scrape_date) VALUES (?, ?, ?, ?)",
                            (store_id, product_code, price, today_str)
                        )
                        prices_changed += 1
            
            conn.execute("UPDATE stores SET last_scraped_date = ? WHERE store_id = ?", (today_str, store_id))
            conn.commit()
            print(f"Finished store {store_id}. Processed {items_processed} items, recorded {prices_changed} price changes.")

        except requests.exceptions.RequestException as e:
            print(f"  -> FAILED to get data for store {store_id}: {e}")
        
        time.sleep(2)

    conn.close()
    print("\n--- Scrape complete! ---")

if __name__ == "__main__":
    main()