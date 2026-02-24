import json
from typing import List, Optional, TypedDict, Set
from utils.logger import setup_logger
from .safe_file_manager import RedisConfigStore, tds_file

logger = setup_logger()


class TDSRate(TypedDict):
    section: str
    nature_of_transaction: str
    threshold_limit: int
    tds_rate: float


class TDSManager:
    def __init__(self, file_manager: RedisConfigStore):
        self.file_manager = file_manager
        self.data: List[TDSRate] = []

    def load_data(self) -> List[TDSRate]:
        """Reads JSON safely via SafeFileManager"""
        try:
            content = self.file_manager.read()

            if not content:
                logger.warning("TDS file is empty")
                self.data = []
                return []

            self.data = json.loads(content)
            return self.data

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in TDS file: {e}")
            self.data = []
            return []

    def save_data(self, data: List[TDSRate]):
        """Writes JSON safely via SafeFileManager"""
        try:
            json_string = json.dumps(data, indent=2)
            self.file_manager.write(json_string)
            self.data = data
        except Exception as e:
            logger.error(f"Failed to write TDS data: {e}")

    def get_all_transaction_natures(self) -> Set[str]:
        try:
            return {
                item["nature_of_transaction"]
                for item in self.data
            }
        except KeyError as e:
            logger.error(f"Data mapping error: Missing key {e}")
            return set()

    def get_transaction_by_nature(self, nature_name: str) -> Optional[TDSRate]:
        try:
            search_term = nature_name.strip().lower()

            for item in self.data:
                if item["nature_of_transaction"].strip().lower() == search_term:
                    return item

            return None

        except Exception as e:
            logger.error(f"Unexpected error during search: {e}")
            return None
        
        
MANAGER = TDSManager(tds_file)


