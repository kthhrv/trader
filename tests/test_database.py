import unittest
import os
import sqlite3
from src.database import init_db, get_db_connection, fetch_recent_trades, fetch_trade_data, save_post_mortem, DB_PATH

class TestDatabase(unittest.TestCase):
    
    def setUp(self):
        # Use a temporary database file for testing
        self.test_db_path = "data/test_trader.db"
        # Patch the DB_PATH in src.database (we'll need to do this carefully or just rely on the fact that we can control the path if we modify the module, but monkeypatching a global constant in a test is cleaner)
        # Actually, easiest way without complex patching is to just ensure we use a test path if possible, or mock sqlite3.connect.
        # Let's mock sqlite3.connect to point to a memory DB or a temp file.
        pass

    def tearDown(self):
        # Cleanup
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)

    def test_init_db(self):
        # We need to mock the DB_PATH used in init_db or ensure it uses our test path
        # Since DB_PATH is hardcoded in src/database.py, let's mock sqlite3.connect
        # to open our test DB instead.
        import src.database
        original_db_path = src.database.DB_PATH
        src.database.DB_PATH = self.test_db_path
        
        try:
            init_db()
            self.assertTrue(os.path.exists(self.test_db_path))
            
            conn = sqlite3.connect(self.test_db_path)
            cursor = conn.cursor()
            
            # Check tables exist
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trade_log'")
            self.assertIsNotNone(cursor.fetchone())            
            # Check columns (migration test)
            cursor.execute("PRAGMA table_info(trade_log)")
            columns = [row[1] for row in cursor.fetchall()]
            self.assertIn('post_mortem', columns)
            
            conn.close()
        finally:
            src.database.DB_PATH = original_db_path

    def test_fetch_recent_trades(self):
        import src.database
        original_db_path = src.database.DB_PATH
        src.database.DB_PATH = self.test_db_path
        
        try:
            init_db()
            conn = sqlite3.connect(self.test_db_path)
            cursor = conn.cursor()
            
            # Insert dummy trades
            cursor.execute("INSERT INTO trade_log (timestamp, epic, action, entry, deal_id) VALUES ('2023-01-01T10:00:00', 'A', 'BUY', 100, '1')")
            cursor.execute("INSERT INTO trade_log (timestamp, epic, action, entry, deal_id) VALUES ('2023-01-01T11:00:00', 'B', 'SELL', 200, '2')")
            cursor.execute("INSERT INTO trade_log (timestamp, epic, action, entry, deal_id) VALUES ('2023-01-01T12:00:00', 'C', 'BUY', 300, '3')")
            conn.commit()
            conn.close()
            
            trades = fetch_recent_trades(limit=2)
            self.assertEqual(len(trades), 2)
            self.assertEqual(trades[0]['epic'], 'C') # Most recent first
            self.assertEqual(trades[1]['epic'], 'B')
            
        finally:
            src.database.DB_PATH = original_db_path

    def test_fetch_trade_data_and_save_post_mortem(self):
        import src.database
        original_db_path = src.database.DB_PATH
        src.database.DB_PATH = self.test_db_path
        
        try:
            init_db()
            conn = sqlite3.connect(self.test_db_path)
            cursor = conn.cursor()
            
            deal_id = "test_deal_123"
            cursor.execute("INSERT INTO trade_log (deal_id, epic) VALUES (?, ?)", (deal_id, "TEST.EPIC"))
            
            conn.commit()
            conn.close()
            
            # Test Fetch
            data = fetch_trade_data(deal_id)
            self.assertIsNotNone(data)
            self.assertEqual(data['log']['epic'], "TEST.EPIC")
            
            # Test Save Post Mortem
            save_post_mortem(deal_id, "Analysis Report")
            
            conn = sqlite3.connect(self.test_db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT post_mortem FROM trade_log WHERE deal_id=?", (deal_id,))
            row = cursor.fetchone()
            self.assertEqual(row[0], "Analysis Report")
            conn.close()
            
        finally:
            src.database.DB_PATH = original_db_path

if __name__ == '__main__':
    unittest.main()
