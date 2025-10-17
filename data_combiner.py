# Coder's Note:
# This script is the heart of our data integration pipeline. It takes the
# nutrition data (which has categories but no pricing codes) and the product
# code map (which links names to pricing codes) and merges them. It also
# generates a report detailing which items were successfully matched and
# which were not. The final result is a single, authoritative CSV file.
# Last checked: October 16, 2025

import pandas as pd

# --- Configuration ---
NUTRITION_FILE = "nutrition.csv"
CODE_MAP_FILE = "product_code_map.csv"
OUTPUT_FILE = "products_master.csv"

def normalize_name(name):
    """
    A simple normalizer to improve matching between the two files.
    Removes special characters and extra whitespace.
    """
    if not isinstance(name, str):
        return ""
    # Lowercase, remove ® and ™, and strip whitespace
    return name.lower().replace('®', '').replace('™', '').strip()

def main():
    """
    Merges nutrition data with product codes based on item name and
    generates a reconciliation report.
    """
    print("--- Starting data merge process ---")
    try:
        nutrition_df = pd.read_csv(NUTRITION_FILE)
        codes_df = pd.read_csv(CODE_MAP_FILE)
        print(f"Loaded {len(nutrition_df)} rows from nutrition source ('{NUTRITION_FILE}')")
        print(f"Loaded {len(codes_df)} rows from product code source ('{CODE_MAP_FILE}')")

        # Normalize the 'item_name' column in both dataframes for better matching
        nutrition_df['normalized_name'] = nutrition_df['item_name'].apply(normalize_name)
        codes_df['normalized_name'] = codes_df['item_name'].apply(normalize_name)

        # Perform an outer merge to keep all records from both files and identify the source
        merged_df = pd.merge(
            nutrition_df, 
            codes_df[['product_code', 'normalized_name']], 
            on='normalized_name', 
            how='outer', 
            indicator=True
        )

        # --- Generate Reconciliation Report ---
        matched_items = merged_df[merged_df['_merge'] == 'both']
        nutrition_only = merged_df[merged_df['_merge'] == 'left_only']
        codes_only = merged_df[merged_df['_merge'] == 'right_only']

        print("\n--- Data Reconciliation Report ---")
        print(f"\n[SUCCESS] {len(matched_items)} items matched between both files.")
        
        print(f"\n[INFO] {len(nutrition_only)} items found ONLY in nutrition file (no price code):")
        for index, row in nutrition_only.iterrows():
            print(f"  - {row['item_name']}")

        print(f"\n[INFO] {len(codes_only)} items found ONLY in product code file (no nutrition info):")
        # For codes_only, the original name is in the 'item_name_y' column from the merge
        # We need to re-merge to get the original name from codes_df
        codes_only_names = pd.merge(codes_only[['normalized_name']], codes_df, on='normalized_name', how='left')
        for index, row in codes_only_names.iterrows():
            print(f"  - {row['item_name']} (Code: {row['product_code']})")

        # Clean up the final dataframe
        final_df = matched_items.drop(columns=['normalized_name', '_merge'])
        final_df = final_df.drop_duplicates(subset=['product_code'])

        # Save the final master file
        final_df.to_csv(OUTPUT_FILE, index=False)
        print(f"\nSuccessfully merged data into '{OUTPUT_FILE}'.")
        print(f"Final product count: {len(final_df)}")

    except FileNotFoundError as e:
        print(f"Error: Make sure '{NUTRITION_FILE}' and '{CODE_MAP_FILE}' exist. {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    main()