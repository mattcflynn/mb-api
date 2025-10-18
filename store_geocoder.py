# Coder's Note:
# This script has been upgraded to use a manual override CSV to fix
# problematic addresses (like intersections) before sending them to the
# geocoding service. This improves the success rate and data quality.
# Last checked: October 1, 2025

import csv
import time
import os
import re
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
import googlemaps

# --- Configuration ---
INPUT_CSV_FILENAME = "taco_bell_100_stores_with_ids.csv"
OUTPUT_CSV_FILENAME = "taco_bell_100_stores_with_coords.csv"
MANUAL_FIXES_CSV = "manual_addresses.csv" # NEW: Manual override file

# NEW: Add your Google Maps API Key as an environment variable for security
# On Mac/Linux: export GOOGLE_MAPS_API_KEY="YOUR_KEY"
# On Windows: set GOOGLE_MAPS_API_KEY="YOUR_KEY"
GOOGLE_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")

def load_manual_fixes(filename):
    """Loads a CSV of address corrections into a dictionary."""
    fixes = {}
    try:
        with open(filename, mode='r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                fixes[row['original_address']] = row['corrected_address']
        print(f"Successfully loaded {len(fixes)} manual address corrections.")
    except FileNotFoundError:
        print("Info: No manual address correction file found. Continuing without it.")
    return fixes

def generate_clean_attempts(address):
    """
    Generates a series of progressively cleaned versions of an address.
    """
    attempts = []
    # 1. Remove suite/unit numbers
    cleaned = re.sub(r'\s+(?:Ste|Suite|#|Unit)\s*[\w# -]+$', '', address, flags=re.IGNORECASE).strip()
    if cleaned != address:
        attempts.append(cleaned)
    else:
        # If no suite was removed, start with the original for the next step
        attempts.append(address)

    # 2. Expand common abbreviations on the latest cleaned version
    # Using word boundaries (\b) to avoid replacing parts of words
    attempts.append(re.sub(r'\bRd\.?$', 'Road', attempts[-1], flags=re.IGNORECASE).strip())
    attempts.append(re.sub(r'\bSt\.?$', 'Street', attempts[-1], flags=re.IGNORECASE).strip())
    return list(dict.fromkeys(attempts)) # Return unique attempts

def main():
    """
    Reads a CSV of store addresses, applies manual corrections, geocodes
    each one, and saves the enriched data to a new file.
    """
    # --- NEW: Load manual corrections first ---
    address_fixes = load_manual_fixes(MANUAL_FIXES_CSV)

    stores_to_geocode = []
    try:
        with open(INPUT_CSV_FILENAME, mode='r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                if row.get('full_address') and row['full_address'] != 'ERROR':
                    stores_to_geocode.append(row)
    except FileNotFoundError:
        print(f"ERROR: The input file '{INPUT_CSV_FILENAME}' was not found.")
        return

    print(f"Found {len(stores_to_geocode)} stores with valid addresses to geocode.")

    geolocator = Nominatim(user_agent="macrobell_store_locator_1.1")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)

    gmaps = None
    if GOOGLE_API_KEY:
        gmaps = googlemaps.Client(key=GOOGLE_API_KEY)
        print("Google Maps API key found. Will use as a fallback.")

    enriched_stores = []
    failed_addresses = []
    for i, store in enumerate(stores_to_geocode):
        original_address = store['full_address']
        
        # --- NEW: Check for and apply a manual fix ---
        address_to_geocode = address_fixes.get(original_address, original_address)
        
        print(f"Processing {i+1}/{len(stores_to_geocode)}: {address_to_geocode}")
        if original_address in address_fixes:
            print(f"  -> Applied manual fix for: '{original_address}'")

        try:
            location = geocode(address_to_geocode, addressdetails=True, timeout=10)
            if location:
                store['latitude'] = location.latitude
                store['longitude'] = location.longitude
                print(f"  -> Success: ({location.latitude}, {location.longitude})")
            else:
                # --- NEW: Automated Retry Logic ---
                print("  -> Initial attempt failed. Trying automated cleaning...")
                clean_attempts = generate_clean_attempts(address_to_geocode)
                for attempt in clean_attempts:
                    if attempt != address_to_geocode: # Don't retry the same address
                        print(f"    - Retrying with: '{attempt}'")
                        time.sleep(1) # Be polite to the API
                        location = geocode(attempt, addressdetails=True, timeout=10)
                        if location:
                            print(f"  -> Success on retry: ({location.latitude}, {location.longitude})")
                            store['latitude'] = location.latitude
                            store['longitude'] = location.longitude
                            break # Exit the retry loop on success
                
                # --- NEW: Google Maps API Fallback ---
                if not location and gmaps:
                    print("    - Retrying with Google Maps API...")
                    try:
                        geocode_result = gmaps.geocode(address_to_geocode)
                        if geocode_result:
                            lat = geocode_result[0]['geometry']['location']['lat']
                            lng = geocode_result[0]['geometry']['location']['lng']
                            print(f"  -> Success with Google: ({lat}, {lng})")
                            store['latitude'] = lat
                            store['longitude'] = lng
                            location = True # Set a flag to indicate success
                    except Exception as google_e:
                        print(f"    - Google API Error: {google_e}")

                if not location: # This will be true if all attempts failed
                    print("  -> FAILED: All automated attempts failed.")
                    store['latitude'] = 'N/A'
                    store['longitude'] = 'N/A'
                    failed_addresses.append(original_address) # Log the original for manual fixing
            
            enriched_stores.append(store)
        except Exception as e:
            print(f"  -> ERROR: An exception occurred: {e}")
            store['latitude'] = 'ERROR'
            store['longitude'] = 'ERROR'
            failed_addresses.append(address_to_geocode)
            enriched_stores.append(store)

    # --- SAVE THE ENRICHED DATA ---
    if not enriched_stores:
        print("No stores were successfully geocoded. Exiting.")
        return
        
    print(f"\n--- Saving {len(enriched_stores)} stores with coordinates to {OUTPUT_CSV_FILENAME} ---")
    
    fieldnames = list(enriched_stores[0].keys())
    
    with open(OUTPUT_CSV_FILENAME, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(enriched_stores)
        
    print("Successfully created the geocoded store data file.")

    # --- NEW: Report on any failures ---
    if failed_addresses:
        print("\n--- Geocoding Failures ---")
        print(f"{len(failed_addresses)} addresses could not be geocoded. You may want to add them to '{MANUAL_FIXES_CSV}':")
        for address in failed_addresses:
            print(f'"{address}",""')

if __name__ == "__main__":
    main()