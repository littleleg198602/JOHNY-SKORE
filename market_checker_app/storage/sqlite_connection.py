"""SQLite transactions that also release their file handle on context exit."""
from __future__ import annotations

import sqlite3


class ClosingSQLiteConnection(sqlite3.Connection):
    # sqlite3.Connection's own context manager commits/rolls back but does not
    # close. Relying on garbage collection leaves databases locked on Windows.
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()
