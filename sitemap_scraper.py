# Coder's Note:
# This script gets a list of all Taco Bell locations by first parsing the
# main sitemap index, and then parsing each of the sub-sitemaps listed there.
# Last checked: September 15, 2025

import requests
import xml.etree.ElementTree as ET
import csv
import re

from macrobell.config import SITEMAP_INDEX_URL, FULL_STORE_LIST_CSV

OUTPUT_CSV_FILENAME = FULL_STORE_LIST_CSV

def get_urls_from_sitemap(sitemap_url, headers):
    """Downloads and parses a sitemap (or sitemap index), returning a list of all URLs found."""
    print(f"Fetching sitemap: {sitemap_url}")
    try:
        response = requests.get(sitemap_url, headers=headers)
        response.raise_for_status() # Raise an error for bad responses (4xx or 5xx)
        
        # We need to register the XML namespace to parse the file correctly
        namespaces = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        root = ET.fromstring(response.content)
        
        # This will find <loc> tags in either a sitemap or a sitemap index
        urls = [elem.text for elem in root.findall('.//ns:loc', namespaces)]
        return urls
    except requests.exceptions.RequestException as e:
        print(f"Error fetching sitemap {sitemap_url}: {e}")
        return []
    except ET.ParseError as e:
        print(f"Error parsing XML from {sitemap_url}: {e}")
        return []

def main():
    """
    Finds all Taco Bell store URLs from the website's sitemap index and saves them.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
    }
    
    all_store_details = []
    
    # --- STAGE 1: Get the list of sub-sitemaps from the main index file ---
    sub_sitemap_urls = get_urls_from_sitemap(SITEMAP_INDEX_URL, headers)
    
    if not sub_sitemap_urls:
        print("Could not retrieve any URLs from the main sitemap index. Exiting.")
        return

    print(f"Found {len(sub_sitemap_urls)} sub-sitemaps in the index.")

    all_store_urls = []
    # --- STAGE 2: Loop through each sub-sitemap and get the store URLs ---
    for sitemap_url in sub_sitemap_urls:
        store_urls_from_sitemap = get_urls_from_sitemap(sitemap_url, headers)
        if store_urls_from_sitemap:
            all_store_urls.extend(store_urls_from_sitemap)

    print(f"\nFound {len(all_store_urls)} total URLs across all sitemaps. Filtering for store pages...")

    # This regex will match a typical store URL and extract the state, city, and address slug
    store_pattern = re.compile(r"https://locations\.tacobell\.com/([a-z]{2})/([^/]+)/([^/]+)\.html")

    for url in all_store_urls:
        match = store_pattern.match(url)
        if match:
            state, city, address_slug = match.groups()
            all_store_details.append({
                'url': url,
                'state': state,
                'city': city,
                'address_slug': address_slug
            })

    if not all_store_details:
        print("Found URLs, but none matched the expected store page format.")
        return

    # --- SAVE RESULTS TO CSV ---
    print(f"\n--- Found {len(all_store_details)} store pages. Saving to {OUTPUT_CSV_FILENAME} ---")
    with open(OUTPUT_CSV_FILENAME, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=['url', 'state', 'city', 'address_slug'])
        writer.writeheader()
        writer.writerows(all_store_details)
    
    print("Successfully saved all found store URLs.")


if __name__ == "__main__":
    main()

