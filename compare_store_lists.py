# Coder's Note:
# This is a maintenance utility script to help keep the store database current.
# It compares the latest output from the sitemap_scraper.py against the
# final master list of geocoded stores. It then generates a report detailing
# any new stores that have opened and any stores that appear to have closed
# (i.e., are no longer listed in the sitemap).
# Last checked: October 17, 2025

import pandas as pd

# --- Configuration ---
# The latest list of stores scraped from the sitemap
NEW_STORE_LIST_CSV = "taco_bell_stores_from_sitemap.csv"

# The current master list of stores that have been fully processed
MASTER_STORE_LIST_CSV = "taco_bell_stores_final_with_coords.csv"

def main():
    """
    Compares the new store list against the master list and reports on
    newly opened and closed stores.
    """
    print("--- Comparing store lists for new and closed locations ---")

    try:
        new_df = pd.read_csv(NEW_STORE_LIST_CSV)
        master_df = pd.read_csv(MASTER_STORE_LIST_CSV)
    except FileNotFoundError as e:
        print(f"ERROR: Could not find a required file. {e}")
        print(f"Please make sure both '{NEW_STORE_LIST_CSV}' and '{MASTER_STORE_LIST_CSV}' exist.")
        return

    print(f"\nLoaded {len(new_df)} stores from the new sitemap scrape.")
    print(f"Loaded {len(master_df)} stores from the master list.")

    # Use the URL as the unique key for comparison
    new_urls = set(new_df['url'])
    master_urls = set(master_df['url'])

    # Find the differences
    added_urls = new_urls - master_urls
    removed_urls = master_urls - new_urls

    print("\n--- Store Comparison Report ---")

    if not added_urls and not removed_urls:
        print("No changes found. The store lists are identical.")
        return

    if added_urls:
        print(f"\n[+] Found {len(added_urls)} new stores (need to be processed):")
        new_stores_df = new_df[new_df['url'].isin(added_urls)]
        for index, store in new_stores_df.iterrows():
            print(f"  - {store['city']}, {store['state']}: {store['url']}")

    if removed_urls:
        print(f"\n[-] Found {len(removed_urls)} closed stores (can be removed from DB):")
        closed_stores_df = master_df[master_df['url'].isin(removed_urls)]
        for index, store in closed_stores_df.iterrows():
            print(f"  - {store['city']}, {store['state']} (ID: {store['store_id']}): {store['url']}")

if __name__ == "__main__":
    main()