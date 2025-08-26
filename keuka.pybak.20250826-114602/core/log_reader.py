# log_reader.py
# Utility for reading and parsing application log files

import os
import re
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path

# Default log file location
DEFAULT_LOG_FILE = Path("/home/pi/KeukaSensorProd/data/logs/application.log")

class LogEntry:
    """Represents a single log entry with parsed components."""
    
    def __init__(self, timestamp: datetime, level: str, logger: str, message: str, raw_line: str):
        self.timestamp = timestamp
        self.level = level
        self.logger = logger
        self.message = message
        self.raw_line = raw_line.strip()
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "timestamp_local": self.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
            "raw_line": self.raw_line,
            "age_seconds": int((datetime.now() - self.timestamp).total_seconds())
        }
    
    def matches_filter(self, filter_text: str) -> bool:
        """Check if entry matches search filter (case-insensitive)."""
        if not filter_text:
            return True
        filter_lower = filter_text.lower()
        return (
            filter_lower in self.message.lower() or
            filter_lower in self.logger.lower() or
            filter_lower in self.level.lower()
        )

class LogReader:
    """Reads and parses application log files."""
    
    # Regex pattern for standard log format: 2025-08-25 15:30:45 [ERROR] keuka.hardware.temperature: Message
    LOG_PATTERN = re.compile(
        r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+\[(\w+)\]\s+([^:]+):\s+(.+)$'
    )
    
    def __init__(self, log_file_path: Optional[Path] = None):
        if log_file_path:
            self.log_file = Path(log_file_path)
        else:
            self.log_file = DEFAULT_LOG_FILE
        self._last_read_time = 0
        self._cached_entries = []
        self._cache_duration = 10  # Cache for 10 seconds
    
    def _parse_log_line(self, line: str) -> Optional[LogEntry]:
        """Parse a single log line into a LogEntry object."""
        match = self.LOG_PATTERN.match(line.strip())
        if not match:
            return None
            
        timestamp_str, level, logger, message = match.groups()
        
        try:
            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
            return LogEntry(timestamp, level, logger, message, line)
        except ValueError:
            return None
    
    def _read_log_file(self, max_lines: int = 500) -> List[str]:
        """Read the last N lines from the log file efficiently."""
        if not self.log_file.exists():
            return []
        
        try:
            # Read the file and get the last max_lines
            with open(self.log_file, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                return lines[-max_lines:] if lines else []
        except Exception as e:
            print(f"Error reading log file {self.log_file}: {e}")
            return []
    
    def get_recent_entries(self, 
                          max_entries: int = 50, 
                          min_level: str = 'INFO',
                          hours_back: int = 24,
                          use_cache: bool = True) -> List[LogEntry]:
        """
        Get recent log entries with filtering.
        
        Args:
            max_entries: Maximum number of entries to return
            min_level: Minimum log level (DEBUG, INFO, WARNING, ERROR)
            hours_back: How many hours back to look
            use_cache: Whether to use cached results
            
        Returns:
            List of LogEntry objects, newest first
        """
        current_time = time.time()
        
        # Use cache if recent enough
        if use_cache and (current_time - self._last_read_time) < self._cache_duration:
            return self._filter_entries(self._cached_entries, max_entries, min_level, hours_back)
        
        # Read fresh entries
        lines = self._read_log_file(max_lines=1000)  # Read more lines to have buffer
        entries = []
        
        cutoff_time = datetime.now() - timedelta(hours=hours_back)
        level_priority = {'DEBUG': 0, 'INFO': 1, 'WARNING': 2, 'ERROR': 3}
        min_priority = level_priority.get(min_level.upper(), 1)
        
        for line in reversed(lines):  # Process newest first
            entry = self._parse_log_line(line)
            if entry:
                # Check time cutoff
                if entry.timestamp < cutoff_time:
                    continue
                    
                # Check log level
                if level_priority.get(entry.level.upper(), 0) >= min_priority:
                    entries.append(entry)
                    
                # Stop if we have enough entries
                if len(entries) >= max_entries * 2:  # Get extra for filtering
                    break
        
        # Cache the results
        self._cached_entries = entries
        self._last_read_time = current_time
        
        return self._filter_entries(entries, max_entries, min_level, hours_back)
    
    def _filter_entries(self, entries: List[LogEntry], max_entries: int, 
                       min_level: str, hours_back: int) -> List[LogEntry]:
        """Apply final filtering and limiting to entries."""
        cutoff_time = datetime.now() - timedelta(hours=hours_back)
        level_priority = {'DEBUG': 0, 'INFO': 1, 'WARNING': 2, 'ERROR': 3}
        min_priority = level_priority.get(min_level.upper(), 1)
        
        filtered = [
            entry for entry in entries
            if (entry.timestamp >= cutoff_time and 
                level_priority.get(entry.level.upper(), 0) >= min_priority)
        ]
        
        return filtered[:max_entries]
    
    def get_entries_by_filter(self, 
                             filter_text: str, 
                             max_entries: int = 50,
                             min_level: str = 'INFO') -> List[LogEntry]:
        """
        Get log entries matching a search filter.
        
        Args:
            filter_text: Text to search for in message, logger, or level
            max_entries: Maximum entries to return
            min_level: Minimum log level
            
        Returns:
            Filtered list of LogEntry objects
        """
        all_entries = self.get_recent_entries(max_entries=max_entries * 3, min_level=min_level)
        filtered_entries = [
            entry for entry in all_entries 
            if entry.matches_filter(filter_text)
        ]
        
        return filtered_entries[:max_entries]
    
    def get_log_stats(self) -> Dict[str, Any]:
        """Get statistics about recent log entries."""
        entries = self.get_recent_entries(max_entries=200, min_level='DEBUG', hours_back=24)
        
        if not entries:
            return {
                "total_entries": 0,
                "by_level": {},
                "by_logger": {},
                "oldest_entry": None,
                "newest_entry": None
            }
        
        # Count by level
        level_counts = {}
        logger_counts = {}
        
        for entry in entries:
            level_counts[entry.level] = level_counts.get(entry.level, 0) + 1
            logger_counts[entry.logger] = logger_counts.get(entry.logger, 0) + 1
        
        return {
            "total_entries": len(entries),
            "by_level": level_counts,
            "by_logger": logger_counts,
            "oldest_entry": entries[-1].timestamp.isoformat() if entries else None,
            "newest_entry": entries[0].timestamp.isoformat() if entries else None,
            "log_file_exists": self.log_file.exists(),
            "log_file_size": self.log_file.stat().st_size if self.log_file.exists() else 0
        }

# Global log reader instance
log_reader = LogReader()