#!/usr/bin/env python3
"""
Local SQLite storage system for sensor readings with network outage resilience
"""

import sqlite3
import json
from datetime import datetime
import pytz
import logging
import os

class LocalSensorStorage:
    def __init__(self, db_path="/opt/keuka/sensor_data.db"):
        self.db_path = db_path
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        self.init_db()
    
    def init_db(self):
        """Initialize SQLite database with required tables"""
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_ny TEXT NOT NULL,
                data TEXT NOT NULL,
                uploaded INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Add indexes for performance
        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_uploaded 
            ON sensor_readings(uploaded)
        ''')
        
        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_created_at 
            ON sensor_readings(created_at)
        ''')
        
        conn.commit()
        conn.close()
        logging.info(f"Database initialized at {self.db_path}")
    
    def store_reading(self, data):
        """
        Store sensor reading locally with NY timestamp
        
        Args:
            data (dict): Sensor data dictionary with keys like waterTempF, waterLevelInches, etc.
            
        Returns:
            int: The reading ID assigned by SQLite
        """
        ny_tz = pytz.timezone('America/New_York')
        timestamp_ny = datetime.now(ny_tz).isoformat()
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "INSERT INTO sensor_readings (timestamp_ny, data) VALUES (?, ?)",
            (timestamp_ny, json.dumps(data))
        )
        reading_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        logging.info(f"Stored reading {reading_id} at {timestamp_ny}")
        return reading_id
    
    def get_unuploaded(self, limit=100):
        """
        Get readings that haven't been uploaded yet, in chronological order
        
        Args:
            limit (int): Maximum number of readings to return
            
        Returns:
            list: List of tuples (id, timestamp_ny, data_json)
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "SELECT id, timestamp_ny, data FROM sensor_readings WHERE uploaded = 0 ORDER BY id LIMIT ?",
            (limit,)
        )
        readings = cursor.fetchall()
        conn.close()
        
        return readings
    
    def mark_uploaded(self, reading_id):
        """
        Mark a specific reading as successfully uploaded
        
        Args:
            reading_id (int): The database ID of the reading to mark
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "UPDATE sensor_readings SET uploaded = 1 WHERE id = ?",
            (reading_id,)
        )
        conn.commit()
        conn.close()
        
        if cursor.rowcount > 0:
            logging.info(f"Marked reading {reading_id} as uploaded")
        else:
            logging.warning(f"Reading {reading_id} not found for upload marking")
    
    def get_stats(self):
        """
        Get storage statistics
        
        Returns:
            dict: Statistics including total, uploaded, pending counts
        """
        conn = sqlite3.connect(self.db_path)
        
        cursor = conn.execute("SELECT COUNT(*) FROM sensor_readings")
        total = cursor.fetchone()[0]
        
        cursor = conn.execute("SELECT COUNT(*) FROM sensor_readings WHERE uploaded = 1")
        uploaded = cursor.fetchone()[0]
        
        cursor = conn.execute("SELECT COUNT(*) FROM sensor_readings WHERE uploaded = 0")
        pending = cursor.fetchone()[0]
        
        # Get oldest pending reading
        cursor = conn.execute(
            "SELECT timestamp_ny FROM sensor_readings WHERE uploaded = 0 ORDER BY id LIMIT 1"
        )
        oldest_pending = cursor.fetchone()
        oldest_pending = oldest_pending[0] if oldest_pending else None
        
        conn.close()
        
        return {
            'total': total,
            'uploaded': uploaded,
            'pending': pending,
            'oldest_pending': oldest_pending
        }
    
    def cleanup_old(self, days=30):
        """
        Remove uploaded readings older than specified days
        
        Args:
            days (int): Number of days to keep uploaded readings
            
        Returns:
            int: Number of records deleted
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "DELETE FROM sensor_readings WHERE uploaded = 1 AND created_at < datetime('now', '-{} days')".format(days)
        )
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        if deleted_count > 0:
            logging.info(f"Cleaned up {deleted_count} old uploaded readings (older than {days} days)")
        
        return deleted_count
    
    def vacuum_db(self):
        """
        Vacuum the database to reclaim space after cleanup
        """
        conn = sqlite3.connect(self.db_path)
        conn.execute("VACUUM")
        conn.close()
        logging.info("Database vacuumed")


def main():
    """Test the local storage functionality"""
    logging.basicConfig(level=logging.INFO)
    
    # Test with temporary database
    import tempfile
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_sensor.db")
    
    storage = LocalSensorStorage(test_db)
    
    # Test storing some data
    test_data = {
        "waterTempF": 72.5,
        "waterLevelInches": 24.2,
        "turbidityNTU": 15.3,
        "latitude": 42.606,
        "longitude": -77.091,
        "elevationFeet": 710
    }
    
    reading_id = storage.store_reading(test_data)
    print(f"Stored reading with ID: {reading_id}")
    
    # Test retrieving unuploaded
    unuploaded = storage.get_unuploaded()
    print(f"Unuploaded readings: {len(unuploaded)}")
    
    # Test stats
    stats = storage.get_stats()
    print(f"Storage stats: {stats}")
    
    # Test marking as uploaded
    if unuploaded:
        storage.mark_uploaded(unuploaded[0][0])
        print(f"Marked reading {unuploaded[0][0]} as uploaded")
    
    # Test cleanup
    deleted = storage.cleanup_old(0)  # Clean all uploaded immediately for test
    print(f"Cleaned up {deleted} records")
    
    print("Local storage test completed successfully!")


if __name__ == "__main__":
    main()