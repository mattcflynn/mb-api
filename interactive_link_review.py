import csv
import os

# Configuration
REVIEW_FILE = 'link_review_needed_sorted_by_category.csv'
OVERRIDES_FILE = 'link_overrides.csv'

def load_overrides():
    """Loads existing overrides to avoid re-doing work."""
    overrides = set()
    if os.path.exists(OVERRIDES_FILE):
        with open(OVERRIDES_FILE, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['product_code']:
                    overrides.add(row['product_code'])
    return overrides

def append_override(product_code, item_id):
    """Appends a new override to the CSV file."""
    file_exists = os.path.exists(OVERRIDES_FILE)
    # Check if file is empty to write header
    is_empty = file_exists and os.path.getsize(OVERRIDES_FILE) == 0
    
    with open(OVERRIDES_FILE, 'a', newline='') as f:
        writer = csv.writer(f)
        # If file didn't exist or was empty, write header
        if not file_exists or is_empty:
            writer.writerow(['product_code', 'item_id'])
        writer.writerow([product_code, item_id])

def main():
    print("--- Link Review Assistant ---")

    if not os.path.exists(REVIEW_FILE):
        print(f"Error: Could not find review file at {REVIEW_FILE}")
        return

    existing_overrides = load_overrides()
    print(f"Loaded {len(existing_overrides)} existing overrides.")

    # Read all items, filtering out already-overridden codes
    items_to_review = []
    try:
        with open(REVIEW_FILE, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['product_code'] not in existing_overrides:
                    items_to_review.append(row)
    except Exception as e:
        print(f"Error reading review file: {e}")
        return

    # Group by category, then by base_name within each category
    categories = {}
    for item in items_to_review:
        cat = item['category']
        name = item['base_name']
        if cat not in categories:
            categories[cat] = {}
        if name not in categories[cat]:
            categories[cat][name] = []
        categories[cat][name].append(item)

    total_codes = len(items_to_review)
    total_categories = len(categories)
    print(f"{total_codes} codes needing review across {total_categories} categories.")

    # Track codes reviewed this session so we can hide them from counts
    session_reviewed = set()

    try:
        while True:
            # Build category list, excluding categories with 0 remaining items
            cat_list = []
            for cat, products in categories.items():
                remaining_codes = sum(
                    1 for items in products.values()
                    for item in items
                    if item['product_code'] not in session_reviewed
                )
                remaining_products = sum(
                    1 for name, items in products.items()
                    if any(item['product_code'] not in session_reviewed for item in items)
                )
                if remaining_codes > 0:
                    cat_list.append((cat, remaining_codes, remaining_products))

            if not cat_list:
                print("\nAll items have been reviewed!")
                break

            print(f"\n{'=' * 60}")
            for i, (cat, code_count, prod_count) in enumerate(cat_list, 1):
                print(f"  {i}. {cat:<25} ({code_count} codes, {prod_count} products)")
            print(f"  q. Quit")

            pick = input("\nSelect a category: ").strip().lower()
            if pick == 'q':
                print("Exiting...")
                break

            try:
                idx = int(pick) - 1
                if idx < 0 or idx >= len(cat_list):
                    print("Invalid selection.")
                    continue
            except ValueError:
                print("Invalid selection.")
                continue

            chosen_cat = cat_list[idx][0]
            products = categories[chosen_cat]

            # Build list of products still needing review in this category
            product_list = [
                (name, items)
                for name, items in products.items()
                if any(item['product_code'] not in session_reviewed for item in items)
            ]
            unique_count = len(product_list)
            processed_count = 0
            quit_category = False

            for base_name, items in product_list:
                # Filter to only un-reviewed codes for this product
                items = [i for i in items if i['product_code'] not in session_reviewed]
                if not items:
                    continue

                processed_count += 1
                ref_item = items[0]

                print(f"\n[{processed_count}/{unique_count}] Product: {base_name.upper()}")
                print(f"Category: {ref_item['category']}")
                print(f"Affects {len(items)} Product Codes: {[i['product_code'] for i in items]}")

                candidate_name = ref_item.get('top_candidate_name', '')
                candidate_id = ref_item.get('top_candidate_item_id', '')
                similarity = ref_item.get('top_candidate_sim', '0')

                if candidate_name:
                    print(f"--> Suggested Match: {candidate_name} (ID: {candidate_id})")
                    print(f"--> Confidence Score: {similarity}")
                else:
                    print("--> No automated candidate found.")

                while True:
                    choice = input("Action? [y]es / [m]anual ID / [s]kip / [q]uit category: ").lower().strip()

                    if choice == 'y':
                        if not candidate_id:
                            print("No candidate ID available to accept. Please enter Manually.")
                            continue
                        for item in items:
                            append_override(item['product_code'], candidate_id)
                            session_reviewed.add(item['product_code'])
                        print(f"Saved match for {len(items)} codes.")
                        break

                    elif choice == 'm':
                        manual_id = input("Enter Target Item ID: ").strip()
                        if manual_id:
                            for item in items:
                                append_override(item['product_code'], manual_id)
                                session_reviewed.add(item['product_code'])
                            print(f"Saved manual override for {len(items)} codes.")
                            break

                    elif choice == 's':
                        print("Skipped.")
                        break

                    elif choice == 'q':
                        print("Returning to category menu...")
                        quit_category = True
                        break

                if quit_category:
                    break

    except KeyboardInterrupt:
        print("\nExiting...")

if __name__ == "__main__":
    main()