import json
from collections import Counter

def verify_menu_data(filename="menu_031829.json"):
    """
    Loads menu data from a JSON file and verifies that each item
    has a price and a unique code.

    Args:
        filename (str): The path to the menu JSON file.
    """
    print(f"--- Verifying data in '{filename}' ---")

    try:
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: The file '{filename}' was not found.")
        return
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from '{filename}'.")
        return

    all_products = []
    for category in data.get("menuProductCategories", []):
        all_products.extend(category.get("products", []))

    items_without_price = []
    items_without_code = []
    all_codes = []

    for product in all_products:
        product_name = product.get('name', 'Unknown Product')

        if 'price' not in product or 'value' not in product['price']:
            items_without_price.append(product_name)

        if 'code' not in product or not product['code']:
            items_without_code.append(product_name)
        else:
            all_codes.append(product['code'])

    print(f"Total products found: {len(all_products)}")
    print(f"Products missing a price: {len(items_without_price)}")
    print(f"Products missing a code/SKU: {len(items_without_code)}")

    code_counts = Counter(all_codes)
    duplicate_codes = {code: count for code, count in code_counts.items() if count > 1}

    print(f"Duplicate codes found: {len(duplicate_codes)}")
    if duplicate_codes:
        print(f"Duplicate codes: {duplicate_codes}")

    if not items_without_price and not items_without_code and not duplicate_codes:
        print("\nVerification successful! All items have a price and a unique code.")

if __name__ == "__main__":
    # You can change the filename if you save a new menu
    verify_menu_data("menu_031829.json")