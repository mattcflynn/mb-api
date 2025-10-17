# Coder's Note:
# This script represents a major upgrade in data collection strategy.
# Instead of scraping the front-end website, it directly hits the
# official Taco Bell back-end API to retrieve menu data as clean JSON.
# This is faster, more reliable, and more scalable.
# Last checked: October 16, 2025

import requests
import json

# --- Configuration ---
# You can change this to any valid Taco Bell store ID
STORE_ID = "031829"
API_URL = f"https://www.tacobell.com/tacobellwebservices/v4/tacobell/products/menu/{STORE_ID}"

# These headers are copied directly from your successful cURL command
# They make our script look like a legitimate web browser
HEADERS = {
    'sec-ch-ua-platform': '"macOS"',
    'Referer': f'https://www.tacobell.com/food/best-sellers?store={STORE_ID}',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
    'sec-ch-ua': '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
    'DNT': '1',
    'sec-ch-ua-mobile': '?0'
}

def main():
    """
    Fetches the full menu for a given store ID directly from the Taco Bell API
    and saves the raw JSON data to a file.
    """
    print(f"--- Hitting API for store: {STORE_ID} ---")
    
    try:
        # Make the GET request with the specified URL and headers
        response = requests.get(API_URL, headers=HEADERS)
        
        # This will raise an error if the request failed (e.g., 404, 500)
        response.raise_for_status()
        
        # Parse the JSON response into a Python dictionary
        menu_data = response.json()
        
        print("Successfully retrieved data from the API.")
        
        # --- Save the data to a file for inspection ---
        output_filename = f"menu_{STORE_ID}.json"
        with open(output_filename, 'w', encoding='utf-8') as f:
            # Use json.dump for pretty-printing the JSON
            json.dump(menu_data, f, ensure_ascii=False, indent=4)
            
        print(f"Successfully saved menu data to '{output_filename}'")
        print("\nNext step: Analyze the JSON structure to find the item names and prices!")

    except requests.exceptions.RequestException as e:
        print(f"An error occurred while making the API request: {e}")
    except json.JSONDecodeError:
        print("Failed to parse the response as JSON. The API might have changed or there was an error.")
        print("Response Text:", response.text)

if __name__ == "__main__":
    main()