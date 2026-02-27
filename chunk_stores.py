# Coder's Note:
# This is a utility script to solve the problem of processing a very large
# number of stores. It reads the full list of stores and groups them into
# regional chunks of a target size. Crucially, it ensures that all stores
# from a single state remain in the same chunk, preventing states from
# being split across multiple files.
# Last checked: October 17, 2025

import pandas as pd
from pathlib import Path

from macrobell.config import FULL_STORE_LIST_CSV, CHUNKS_DIR

# --- Configuration ---
OUTPUT_DIR = CHUNKS_DIR
TARGET_CHUNK_SIZE = 500

def main():
    """
    Reads a large CSV of stores and splits it into smaller, state-aware chunks.
    """
    print("--- Starting store chunking process ---")
    
    try:
        df = pd.read_csv(FULL_STORE_LIST_CSV)
        print(f"Loaded {len(df)} total stores from '{FULL_STORE_LIST_CSV}'.")
    except FileNotFoundError:
        print(f"ERROR: The input file '{FULL_STORE_LIST_CSV}' was not found.")
        return

    # Group by state and get the count of stores in each
    state_counts = df['state'].value_counts().sort_index()

    chunks = []
    current_chunk_states = []
    current_chunk_size = 0

    # Greedily pack states into chunks
    for state, count in state_counts.items():
        # If the current chunk is not empty and adding the next state would
        # push it over the target size, finalize the current chunk.
        if current_chunk_size > 0 and (current_chunk_size + count) > TARGET_CHUNK_SIZE:
            chunks.append(current_chunk_states)
            current_chunk_states = []
            current_chunk_size = 0
        
        current_chunk_states.append(state)
        current_chunk_size += count

    # Add the last remaining chunk
    if current_chunk_states:
        chunks.append(current_chunk_states)

    # Create the output directory
    output_path = Path(OUTPUT_DIR)
    output_path.mkdir(exist_ok=True)

    print(f"\n--- Saving {len(chunks)} chunks to '{OUTPUT_DIR}' directory ---")
    for i, state_group in enumerate(chunks):
        chunk_df = df[df['state'].isin(state_group)]
        chunk_filename = output_path / f"chunk_{i+1:02d}.csv"
        chunk_df.to_csv(chunk_filename, index=False)
        
        print(f"  - Saved {chunk_filename}: {len(chunk_df)} stores (States: {', '.join(state_group)})")

    print("\n--- Chunking complete! ---")

if __name__ == "__main__":
    main()

