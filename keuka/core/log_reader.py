# log_reader.py
# Utility for reading and parsing application log files

import os
import re
import time
import subprocess
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
        from datetime import timezone
        
        # Handle timezone-aware vs naive datetime comparison
        now = datetime.now(timezone.utc) if self.timestamp.tzinfo else datetime.now()
        
        return {
            "timestamp": self.timestamp.isoformat(),
            "timestamp_local": self.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
            "raw_line": self.raw_line,
            "age_seconds": int((now - self.timestamp).total_seconds())
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
    
    # Regex pattern for journalctl format: 2025-08-28T15:10:25-0400 hostname service[pid]: message
    JOURNALCTL_PATTERN = re.compile(
        r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[-+]\d{4})\s+\S+\s+\S+\[\d+\]:\s+(.+)$'
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
        line = line.strip()
        
        # Try standard log format first
        match = self.LOG_PATTERN.match(line)
        if match:
            timestamp_str, level, logger, message = match.groups()
            try:
                timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                return LogEntry(timestamp, level, logger, message, line)
            except ValueError:
                pass
        
        # Try journalctl format
        match = self.JOURNALCTL_PATTERN.match(line)
        if match:
            timestamp_str, message = match.groups()
            try:
                from datetime import timezone
                # Parse ISO format timestamp - fix timezone format for Python
                # Convert -0400 to -04:00 format
                if timestamp_str[-5] in '+-' and timestamp_str[-4:].isdigit():
                    timestamp_str = timestamp_str[:-2] + ':' + timestamp_str[-2:]
                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                # Convert to UTC for consistent comparison
                timestamp = timestamp.astimezone(timezone.utc)
                
                # Extract log level and logger from message if possible
                level = 'INFO'  # Default level
                logger = 'keuka'  # Default logger
                
                # Look for common log patterns in the message
                if 'ERROR' in message.upper() or 'FAILED' in message.upper():
                    level = 'ERROR'
                elif 'WARNING' in message.upper() or 'WARN' in message.upper():
                    level = 'WARNING'
                elif 'DEBUG' in message.upper():
                    level = 'DEBUG'
                
                # Try to extract logger from message
                if ': ' in message:
                    parts = message.split(': ', 1)
                    if '.' in parts[0] and len(parts[0]) < 50:  # Looks like a logger name
                        logger = parts[0]
                        message = parts[1]
                
                return LogEntry(timestamp, level, logger, message, line)
            except (ValueError, TypeError):
                pass
        
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
    
    def _read_journalctl_logs(self, max_lines: int = 500, hours_back: int = 24) -> List[str]:
        """Read logs from journalctl for keuka-sensor service."""
        try:
            # Get logs from keuka-sensor service for the specified time period
            cmd = [
                'journalctl', 
                '-u', 'keuka-sensor', 
                '--no-pager', 
                f'--since={hours_back} hours ago',
                f'-n', str(max_lines),
                '--output=short-iso'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                print(f"Error running journalctl: {result.stderr}")
                return []
            
            lines = result.stdout.strip().split('\n')
            # Filter out the header line and empty lines
            return [line for line in lines if line and not line.startswith('-- ')]
            
        except subprocess.TimeoutExpired:
            print("Timeout reading journalctl logs")
            return []
        except Exception as e:
            print(f"Error reading journalctl logs: {e}")
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
        
        # Read fresh entries from both file and journalctl
        file_lines = self._read_log_file(max_lines=1000)  # Read more lines to have buffer
        journal_lines = self._read_journalctl_logs(max_lines=1000, hours_back=hours_back)
        
        # Combine all lines
        all_lines = file_lines + journal_lines
        entries = []
        
        from datetime import timezone
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        level_priority = {'DEBUG': 0, 'INFO': 1, 'WARNING': 2, 'ERROR': 3}
        min_priority = level_priority.get(min_level.upper(), 1)
        
        for line in all_lines:  # Process all lines
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
        
        # Remove duplicates and sort by timestamp (newest first)
        unique_entries = []
        seen_lines = set()
        
        for entry in entries:
            # Use a combination of timestamp and message to identify duplicates
            key = f"{entry.timestamp.isoformat()}|{entry.message[:100]}"
            if key not in seen_lines:
                seen_lines.add(key)
                unique_entries.append(entry)
        
        # Sort by timestamp, newest first
        unique_entries.sort(key=lambda e: e.timestamp, reverse=True)
        
        # Cache the results
        self._cached_entries = unique_entries
        self._last_read_time = current_time
        
        return self._filter_entries(unique_entries, max_entries, min_level, hours_back)
    
    def _filter_entries(self, entries: List[LogEntry], max_entries: int, 
                       min_level: str, hours_back: int) -> List[LogEntry]:
        """Apply final filtering and limiting to entries."""
        from datetime import timezone
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours_back)
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