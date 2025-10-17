# Coder's Note:
# This is the refactored database setup script. It now uses product_code
# as the primary key for menu items, making the database more robust. It
# reconciles our name-based nutrition CSV with a product code map generated
# by the code_mapper.py script.
# Last checked: October 16, 2025

import sqlite3
import csv
import re

# --- Configuration ---
DB_FILENAME = "macrobell.db"
STORES_CSV = "taco_bell_stores_or_with_coords.csv"
NUTRITION_CSV = "nutrition.csv"
CODE_MAP_CSV = "product_code_map.csv" # The new mapping file

def clean_numeric_value(value_str):
    """
    A helper function to clean up a string before converting it to a number.
    It removes all non-digit characters, except for a single decimal point.
    Returns '0' if the input is empty or invalid.
    """
    if not value_str:
        return '0'
    # Use regex to remove anything that isn't a digit or a decimal point
    cleaned = re.sub(r'[^\d.]', '', value_str)
    return cleaned if cleaned else '0'

def main():
    """
    Creates and initializes the SQLite database with stores and product info.
    """
    print(f"--- Creating database: {DB_FILENAME} ---")
    conn = sqlite3.connect(DB_FILENAME)
    cur = conn.cursor()

    # --- REFACTORED: Create Tables with product_code ---
    # Dropping tables to ensure a clean slate on each run of this setup script
    cur.execute("DROP TABLE IF EXISTS prices")
    cur.execute("DROP TABLE IF EXISTS products")
    cur.execute("DROP TABLE IF EXISTS stores")
    
    cur.execute("""
    CREATE TABLE stores (
        store_id TEXT PRIMARY KEY,
        full_address TEXT,
        city TEXT,
        state TEXT,
        zip_code TEXT,
        latitude REAL,
        longitude REAL,
        last_scraped_date TEXT
    )""")

    # The nutrition table is now the 'products' table, keyed by product_code
    cur.execute("""
    CREATE TABLE products (
        product_code TEXT PRIMARY KEY,
        item_name TEXT,
        calories INTEGER,
        protein_g REAL,
        carbs_g INTEGER,
        sugar_g INTEGER,
        total_fat_g REAL,
        sat_fat_g REAL,
        trans_fat_g REAL,
        cholesterol_mg INTEGER,
        sodium_mg INTEGER,
        dietary_fiber_g INTEGER
    )""")

    cur.execute("""
    CREATE TABLE prices (
        price_id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT,
        product_code TEXT,
        price REAL,
        scrape_date TEXT,
        FOREIGN KEY(store_id) REFERENCES stores(store_id),
        FOREIGN KEY(product_code) REFERENCES products(product_code)
    )""")
    print("Tables created successfully with product_code as key.")
    conn.commit()

    # --- Populate Stores Table ---
    print(f"\n--- Populating stores from '{STORES_CSV}' ---")
    try:
        with open(STORES_CSV, mode='r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            stores_to_insert = []
            for row in reader:
                if row.get('store_id') and row['store_id'] != 'ERROR' and row.get('latitude') not in ['N/A', 'ERROR']:
                    stores_to_insert.append((
                        row['store_id'], row['full_address'], row['city'], row['state'],
                        row['zip_code'], float(row['latitude']), float(row['longitude'])
                    ))
            
            cur.executemany("""
            INSERT OR IGNORE INTO stores (store_id, full_address, city, state, zip_code, latitude, longitude)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, stores_to_insert)
            
            conn.commit()
            print(f"Successfully inserted or updated {len(stores_to_insert)} stores.")

    except FileNotFoundError:
        print(f"ERROR: Store data file '{STORES_CSV}' not found.")
    except Exception as e:
        print(f"An error occurred while populating stores: {e}")

    # --- REFACTORED: Populate Products Table ---
    print(f"\n--- Reconciling and populating products from '{NUTRITION_CSV}' and '{CODE_MAP_CSV}' ---")
    try:
        # Load the code map into memory for easy lookup
        with open(CODE_MAP_CSV, mode='r', encoding='utf-8') as file:
            code_map = {row['item_name']: row['product_code'] for row in csv.DictReader(file)}

        with open(NUTRITION_CSV, mode='r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            products_to_insert = []
            for row in reader:
                item_name = row['Item Name']
                product_code = code_map.get(item_name) # Find the corresponding product code
                
                if product_code:
                    products_to_insert.append((
                        product_code, item_name,
                        int(float(clean_numeric_value(row.get('Calories')))),
                        float(clean_numeric_value(row.get('Protein (g)'))),
                        int(float(clean_numeric_value(row.get('Carbs (g)')))),
                        int(float(clean_numeric_value(row.get('Sugar (g)')))),
                        float(clean_numeric_value(row.get('Total Fat (g)'))),
                        float(clean_numeric_value(row.get('Sat. Fat (g)'))),
                        float(clean_numeric_value(row.get('Trans Fat (g)'))),
                        int(float(clean_numeric_value(row.get('Cholesterol (mg)')))),
                        int(float(clean_numeric_value(row.get('Sodium (mg)')))),
                        int(float(clean_numeric_value(row.get('Dietary Fiber (g)'))))
                    ))
            
            cur.executemany("""
            INSERT OR IGNORE INTO products (product_code, item_name, calories, protein_g, 
            carbs_g, sugar_g, total_fat_g, sat_fat_g, trans_fat_g, cholesterol_mg, sodium_mg, dietary_fiber_g)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, products_to_insert)

            conn.commit()
            print(f"Successfully matched and inserted {len(products_to_insert)} products.")

    except FileNotFoundError:
        print(f"ERROR: Ensure both '{NUTRITION_CSV}' and '{CODE_MAP_CSV}' exist.")
    except Exception as e:
        print(f"An error occurred while populating products: {e}")

    conn.close()
    print("\n--- Database setup complete! ---")

if __name__ == "__main__":
    main()