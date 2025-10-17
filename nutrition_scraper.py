# Coder's Note:
# This is the definitive version of the nutrition scraper. It hits a direct
# JSON endpoint and reverse-engineers the nutrition calculator's logic
# to assemble the nutritional data for each item from its base ingredients.
# Last checked: October 16, 2025

import requests
import json
import csv
import gzip
import io

# --- Configuration ---
# This is the direct URL to the gzipped nutrition data file.
NUTRITION_DATA_URL = "https://d2eawub7utcl6.cloudfront.net/calculator/10197-0-1760620706.json.gz"
OUTPUT_CSV_FILENAME = "nutrition.csv"

# Filtering logic remains the same
EXCLUDED_ITEM_SUFFIXES = [
    "box", "meal", "combo", "pack", "party", "serves 4",
    "for 2", "for 4", "12 pack", "2 pack"
]

def clean_text(text):
    """A helper function to clean up text."""
    return ' '.join(str(text).strip().split()) if text else ''

def main():
    """Fetches and processes nutrition data from the direct JSON endpoint."""
    print(f"--- Fetching nutrition data from ---\n{NUTRITION_DATA_URL}")
    
    try:
        response = requests.get(NUTRITION_DATA_URL, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        
        # Handle both gzipped and plain JSON responses from the server.
        try:
            # First, try to decompress as a gzipped file.
            with gzip.GzipFile(fileobj=io.BytesIO(response.content)) as gz:
                nutrition_data = json.load(gz)
            print("Successfully downloaded and parsed gzipped JSON.")
        except gzip.BadGzipFile:
            # If it's not a gzipped file, parse it as plain text JSON.
            print("File is not gzipped. Parsing as plain JSON.")
            nutrition_data = response.json()
            print("Successfully parsed plain JSON.")

        # --- REFACTORED: Calculate nutrition from ingredients ---
        calculator_data = nutrition_data.get('calculator')
        if not calculator_data:
            print("Could not find 'calculator' object in the JSON. The structure has changed.")
            return

        # Extract all necessary data blocks for calculation
        items_dict = calculator_data.get('items', {})
        categories_dict = calculator_data.get('categories', {})
        ingredients_dict = calculator_data.get('ingredients', {})
        template_items_dict = calculator_data.get('templateItems', {})
        template_groups_dict = calculator_data.get('templateGroups', {})
        template_multipliers_dict = calculator_data.get('templateGroupMultipliers', {})
        group_ingredients_dict = calculator_data.get('groupIngredients', {})

        if not all([items_dict, categories_dict, ingredients_dict, template_items_dict, template_groups_dict, template_multipliers_dict, group_ingredients_dict]):
            print("One or more required data dictionaries are missing from the JSON. Cannot perform calculation.")
            return

        # Create a lookup map for category IDs to names.
        category_map = {cat_id: cat_data.get('name') for cat_id, cat_data in categories_dict.items()}
        print(f"Mapped {len(category_map)} categories.")

        processed_items = []
        for item_id, base_item_data in items_dict.items():
            item_name = clean_text(base_item_data.get('name'))
            if not item_name: continue
            
            # Skip items with excluded suffixes
            if any(item_name.lower().endswith(suffix) for suffix in EXCLUDED_ITEM_SUFFIXES):
                continue

            # Initialize nutrition totals for the current item
            total_nutrition = {k: 0 for k in ['calories', 'protein', 'total_carb', 'sugars', 'total_fat', 'saturated_fat', 'trans_fat', 'cholesterol', 'sodium', 'fibers']}

            template_id = str(base_item_data.get('template_id'))
            if not template_id:
                continue

            # Get the default ingredients for this item's template
            default_ingredients_for_template = template_items_dict.get(template_id, [])
            default_ingredient_ids = {str(ing['id']) for ing in default_ingredients_for_template if 'id' in ing}

            # Get the ingredient groups for this template
            template_groups = template_groups_dict.get(template_id, [])

            # Iterate through the groups in the template to find default ingredients and calculate nutrition
            for group_info in template_groups:
                group_id = str(group_info.get('group_id'))
                
                # Find the default ingredient for this group
                group_ingredients = group_ingredients_dict.get(group_id, [])
                default_ingredient_id = next((str(ing.get('id')) for ing in group_ingredients if str(ing.get('id')) in default_ingredient_ids), None)
                
                if not default_ingredient_id:
                    continue

                ingredient_data = ingredients_dict.get(default_ingredient_id)
                if not ingredient_data:
                    continue

                # Get the multiplier for this group in this template
                multiplier_info = template_multipliers_dict.get(template_id, {}).get(group_id, {})
                multiplier = float(multiplier_info.get('multiplier', 1.0))
                serving_weight_grams = float(ingredient_data.get('serving_weight', 0) or 0)
                
                if serving_weight_grams == 0:
                    continue

                # Nutrition data is per 100g, so we scale it by the serving weight and multiplier
                scaling_factor = (serving_weight_grams / 100.0) * multiplier

                for key in total_nutrition:
                    value = float(ingredient_data.get(key, 0) or 0)
                    total_nutrition[key] += (value * scaling_factor)

            # Get category name from the map
            category_id = str(base_item_data.get('category_id'))
            category_name = category_map.get(category_id, "Unknown")
            is_breakfast = 1 if 'breakfast' in category_name.lower() else 0

            processed_items.append({
                'item_name': item_name,
                'category': category_name,
                'is_breakfast': is_breakfast,
                'calories': round(total_nutrition['calories']),
                'protein_g': round(total_nutrition['protein'], 1),
                'carbs_g': round(total_nutrition['total_carb']),
                'sugar_g': round(total_nutrition['sugars']),
                'total_fat_g': round(total_nutrition['total_fat'], 1),
                'sat_fat_g': round(total_nutrition['saturated_fat'], 1),
                'trans_fat_g': round(total_nutrition['trans_fat'], 1),
                'cholesterol_mg': round(total_nutrition['cholesterol']),
                'sodium_mg': round(total_nutrition['sodium']),
                'dietary_fiber_g': round(total_nutrition['fibers']),
            })

        if not processed_items:
            print("Could not find any items matching the filters after calculation.")
            return

        print(f"\n--- Saving {len(processed_items)} calculated items to {OUTPUT_CSV_FILENAME} ---")
        fieldnames = processed_items[0].keys()
        with open(OUTPUT_CSV_FILENAME, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(processed_items)

        print("Successfully created the new, filtered nutrition.csv file.")

    except requests.exceptions.RequestException as e:
        print(f"An error occurred during the request: {e}")
    except Exception as e:
        print(f"An overall error occurred: {e}")

if __name__ == "__main__":
    main()