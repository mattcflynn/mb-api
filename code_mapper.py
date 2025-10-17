# Coder's Note:
# This is a one-time utility script to create a mapping between product names
# and their unique product codes by hitting the Taco Bell API for a single store.
# This resulting CSV is essential for enriching our nutrition data.
# Last checked: October 16, 2025

import requests
import csv

# --- Configuration ---
STORE_ID = "031829" # A single, reliable store to get the master menu
OUTPUT_CSV = "product_code_map.csv"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
}

def main():
    """
    Fetches the menu from the API and creates a name-to-code mapping file.
    """
    print(f"--- Creating product code map from store {STORE_ID} ---")
    api_url = f"https://www.tacobell.com/tacobellwebservices/v4/tacobell/products/menu/{STORE_ID}"
    
    try:
        response = requests.get(api_url, headers=HEADERS)
        response.raise_for_status()
        menu_data = response.json()

        product_map = []
        for category in menu_data.get('menuProductCategories', []):
            for product in category.get('products', []):
                name = product.get('name')
                code = product.get('code')
                if name and code:
                    product_map.append({'product_code': code, 'item_name': name})
        
        print(f"Found {len(product_map)} unique products.")

        # Save the mapping to a CSV file
        with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=['product_code', 'item_name'])
            writer.writeheader()
            writer.writerows(product_map)
        
        print(f"Successfully saved product map to '{OUTPUT_CSV}'")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()