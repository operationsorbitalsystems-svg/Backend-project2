import logging
import logging.handlers
import json
from pathlib import Path
from datetime import datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Include extra fields if provided
        if hasattr(record, "extra"):
            log_record.update(record.extra)

        return json.dumps(log_record)


def setup_logger(debug: bool = False):
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    logger = logging.getLogger("invoice_parser")
    logger.handlers = []
    logger.propagate = False

    level = logging.DEBUG if debug else logging.INFO
    logger.setLevel(level)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=10485760,
        backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)

    json_formatter = JsonFormatter()

    file_handler.setFormatter(json_formatter)
    console_handler.setFormatter(json_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger

