# Coder's Note:
# This is the final utility script in the store data collection pipeline.
# Its purpose is to find all the geocoded chunk files (e.g.,
# 'chunk_01_with_coords.csv') and combine them back into a single,
# master file that can be used by the database setup script.
# Last checked: October 17, 2025

import pandas as pd
from pathlib import Path

# --- Configuration ---
CHUNKS_DIR = "store_chunks"
FINAL_OUTPUT_CSV = "taco_bell_stores_final_with_coords.csv"
FILE_PATTERN = "*_with_coords.csv"

def main():
    """
    Finds all processed chunk files, combines them into a single master
    store list, and saves it.
    """
    print("--- Starting chunk combination process ---")
    
    chunks_path = Path(CHUNKS_DIR)
    if not chunks_path.is_dir():
        print(f"ERROR: The directory '{CHUNKS_DIR}' was not found. Please run the chunking and scraping scripts first.")
        return

    chunk_files = sorted(list(chunks_path.glob(FILE_PATTERN)))
    
    if not chunk_files:
        print(f"No processed chunk files ('{FILE_PATTERN}') found in '{CHUNKS_DIR}'.")
        return
        
    print(f"Found {len(chunk_files)} processed chunk files to combine.")
    
    # Read each CSV into a DataFrame and store it in a list
    all_dfs = [pd.read_csv(file) for file in chunk_files]
            
    # Concatenate all DataFrames in the list into a single one
    combined_df = pd.concat(all_dfs, ignore_index=True)
    
    # Sort by state and city for a clean, predictable final file
    combined_df.sort_values(by=['state', 'city'], inplace=True)
    
    combined_df.to_csv(FINAL_OUTPUT_CSV, index=False)
    
    print(f"\n--- Combination complete! ---")
    print(f"Successfully combined {len(combined_df)} stores into '{FINAL_OUTPUT_CSV}'.")

if __name__ == "__main__":
    main()