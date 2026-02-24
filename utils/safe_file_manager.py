from pathlib import Path
from filelock import FileLock
from typing import Union
from config import TDS_FILE_PATH


class SafeFileManager:
    def __init__(self, file_path: Union[str, Path]):
        self.file_path = Path(file_path)
        self.lock = FileLock(str(self.file_path) + ".lock")

        # Ensure parent directory exists
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> str:
        with self.lock:
            if not self.file_path.exists():
                return ""
            return self.file_path.read_text(encoding="utf-8")

    def write(self, content: str):
        temp_file = self.file_path.with_suffix(self.file_path.suffix + ".tmp")

        with self.lock:
            temp_file.write_text(content, encoding="utf-8")
            temp_file.replace(self.file_path)

    def exists(self) -> bool:
        return self.file_path.exists()
    

dr_prompt_file = SafeFileManager("prompts/dr_prompt.txt")
cr_prompt_file = SafeFileManager("prompts/cr_prompt.txt")
tds_prompt_file = SafeFileManager("prompts/tds_nature_prompt.txt")
tds_file = SafeFileManager("data/tds_rates.json")
