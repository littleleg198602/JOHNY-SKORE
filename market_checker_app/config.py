from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_OUTPUT_DIR = Path("outputs")
DEFAULT_DB_PATH = DEFAULT_OUTPUT_DIR / "market_checker_history.db"
DEFAULT_MAX_RSS_ITEMS = 30


@dataclass(slots=True)
class AppConfig:
    output_dir: Path = DEFAULT_OUTPUT_DIR
    marketcap_file: str = ""
    export_excel: bool = True
    compare_previous_run: bool = True
    save_history: bool = True
    sqlite_path: Path = DEFAULT_DB_PATH
    max_rss_items_per_source: int = DEFAULT_MAX_RSS_ITEMS

    def ensure_output_dir(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
