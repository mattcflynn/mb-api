import pandas as pd
import argparse

def sort_csv_by_category(input_file_path, output_file_path):
    """
    Sorts a CSV file by the 'category' column and saves the result.

    Args:
        input_file_path (str): The path to the input CSV file.
        output_file_path (str): The path to save the sorted CSV file.
    """
    try:
        # Read the CSV file into a pandas DataFrame
        df = pd.read_csv(input_file_path)

        # Check if 'category' column exists
        if 'category' not in df.columns:
            print(f"Error: 'category' column not found in {input_file_path}")
            return

        # Sort the DataFrame by the 'category' column
        sorted_df = df.sort_values(by='category', kind='mergesort')

        # Save the sorted DataFrame to a new CSV file
        sorted_df.to_csv(output_file_path, index=False)
        
        print(f"Successfully sorted the file by category.")
        print(f"Sorted data has been saved to: {output_file_path}")

        # Optional: To display the sorted data in the console, you can uncomment the line below
        # print(sorted_df.to_string())

    except FileNotFoundError:
        print(f"Error: The file was not found at {input_file_path}")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Sort a CSV file by the 'category' column.")
    parser.add_argument('--input-file', required=True, help='Path to the input CSV file to sort.')
    parser.add_argument('--output-file', required=True, help='Path to save the sorted output CSV file.')
    
    args = parser.parse_args()
    
    # Run the sorting function
    sort_csv_by_category(args.input_file, args.output_file)
