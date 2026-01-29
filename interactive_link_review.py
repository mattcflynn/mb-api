import csv
import os
import sys

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

    # Read and group items
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

    # Group by base_name to process duplicates together
    grouped_items = {}
    for item in items_to_review:
        name = item['base_name']
        if name not in grouped_items:
            grouped_items[name] = []
        grouped_items[name].append(item)

    unique_count = len(grouped_items)
    print(f"Found {len(items_to_review)} codes needing review, grouped into {unique_count} unique products.")
    print("=" * 60)

    processed_count = 0
    
    try:
        for base_name, items in grouped_items.items():
            processed_count += 1
            ref_item = items[0] # Use first item for display details
            
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
                choice = input("Action? [y]es / [m]anual ID / [s]kip / [q]uit: ").lower().strip()
                
                if choice == 'y':
                    if not candidate_id:
                        print("No candidate ID available to accept. Please enter Manually.")
                        continue
                    for item in items:
                        append_override(item['product_code'], candidate_id)
                    print(f"Saved match for {len(items)} codes.")
                    break
                
                elif choice == 'm':
                    manual_id = input("Enter Target Item ID: ").strip()
                    if manual_id:
                        for item in items:
                            append_override(item['product_code'], manual_id)
                        print(f"Saved manual override for {len(items)} codes.")
                        break
                
                elif choice == 's':
                    print("Skipped.")
                    break
                
                elif choice == 'q':
                    print("Exiting...")
                    sys.exit(0)
                
    except KeyboardInterrupt:
        print("\nExiting...")

if __name__ == "__main__":
    main()