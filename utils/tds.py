import json
from pathlib import Path
from typing import List, Optional, TypedDict, Set
from config import TDS_FILE_PATH
from utils.logger import setup_logger
logger = setup_logger()

# Define the structure for type safety
class TDSRate(TypedDict):
    section: str
    nature_of_transaction: str
    threshold_limit: int
    tds_rate: float

class TDSManager:
    def __init__(self, file_path: str):
        self.file_path: Path = Path(file_path)
        self.data: List[TDSRate] = []

    def load_data(self) -> List[TDSRate]:
        """Reads the JSON file and loads it into the data list."""
        try:
            if not self.file_path.exists():
                raise FileNotFoundError(f"File not found at {self.file_path}")
            
            with open(self.file_path, 'r') as f:
                self.data = json.load(f)
            return self.data
            
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.error(f"Error loading JSON data: {e}")
            return []

    def get_all_transaction_natures(self) -> Set[str]:
        """Returns a single list of all 'nature_of_transaction' values."""
        try:
            # logger.info(f"{self.data}, {[item for item in self.data]}")
            return set([item['nature_of_transaction'] for item in self.data])
        except KeyError as e:
            logger.error(f"Data mapping error: Missing key {e}")
            return []

    def get_transaction_by_nature(self, nature_name: str) -> Optional[TDSRate]:
        """Returns the full object for a given nature of transaction."""
        try:
            # Case-insensitive search with whitespace stripping
            search_term = nature_name.strip().lower()
            for item in self.data:
                if item['nature_of_transaction'].strip().lower() == search_term:
                    return item
            return None
        except Exception as e:
            logger.error(f"An unexpected error occurred during search: {e}")
            return None

MANAGER = TDSManager(TDS_FILE_PATH)


# # --- Example Usage ---
# if __name__ == "__main__":
#     # Define the path to your data

    
#     # 1. Load the data
#     MANAGER.load_data()

#     # 2. Get all names
#     all_names = MANAGER.get_all_transaction_natures()
#     print(f"Loaded {len(all_names)} transaction types.")
#     print(all_names)

#     # 3. Search for a specific one
#     query = "Dividends"
#     result = MANAGER.get_transaction_by_nature(query)
    
#     if result:
#         print(f"Found Section: {result['section']} for {query}")
#     else:
#         print(f"No details found for: {query}")