from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from market_checker_app.storage.sqlite_connection import ClosingSQLiteConnection
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.storage.yahoo_cache_store import YahooCacheStore
from market_checker_app.storage.yahoo_ohlc_cache_store import YahooOhlcCacheStore
from market_checker_app.storage.prediction_label_queue_store import PredictionLabelQueueStore


class SQLiteConnectionLifecycleTests(unittest.TestCase):
    def test_commit_closes_retained_connection_and_releases_database_file(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/"database.db"
            connection = sqlite3.connect(path, factory=ClosingSQLiteConnection)
            with connection as conn:
                conn.execute("CREATE TABLE example (id INTEGER PRIMARY KEY)")
                conn.execute("INSERT INTO example VALUES (1)")
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
            # Keep the Python connection object alive while moving the file;
            # Windows forbids this if the native SQLite handle is still open.
            moved = path.with_name("moved.db")
            path.rename(moved)
            with sqlite3.connect(moved, factory=ClosingSQLiteConnection) as conn:
                self.assertEqual([(1,)], conn.execute("SELECT * FROM example").fetchall())

    def test_failed_transaction_rolls_back_and_also_closes(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/"database.db"
            with sqlite3.connect(path, factory=ClosingSQLiteConnection) as conn:
                conn.execute("CREATE TABLE example (id INTEGER PRIMARY KEY)")
            connection = sqlite3.connect(path, factory=ClosingSQLiteConnection)
            with self.assertRaises(sqlite3.IntegrityError), connection as conn:
                conn.execute("INSERT INTO example VALUES (1)")
                conn.execute("INSERT INTO example VALUES (1)")
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
            with sqlite3.connect(path, factory=ClosingSQLiteConnection) as conn:
                self.assertEqual([], conn.execute("SELECT * FROM example").fetchall())

    def test_all_application_stores_close_retained_connections_after_operations(self):
        with TemporaryDirectory() as directory:
            for index, store_type in enumerate((SQLiteStore, ScoutStore, YahooCacheStore,
                                                YahooOhlcCacheStore, PredictionLabelQueueStore)):
                with self.subTest(store=store_type.__name__):
                    path = Path(directory)/f"store-{index}.db"
                    store = store_type(path)
                    store.ensure_schema()
                    with store._connect() as connection:
                        self.assertEqual((1,), tuple(connection.execute("SELECT 1").fetchone()))
                    with self.assertRaises(sqlite3.ProgrammingError):
                        connection.execute("SELECT 1")
                    path.rename(path.with_suffix(".closed.db"))


if __name__ == "__main__":
    unittest.main()
