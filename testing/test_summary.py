#!/usr/bin/env python3
"""
Generate test summary report
"""
import json
import sys
from pathlib import Path
from datetime import datetime

def generate_summary():
    reports_dir = Path(__file__).parent / "reports"
    
    if not reports_dir.exists():
        print("No test reports found")
        return
        
    # Find most recent report
    reports = list(reports_dir.glob("test_report_*.json"))
    if not reports:
        print("No test reports found")
        return
        
    latest_report = max(reports, key=lambda p: p.stat().st_mtime)
    
    with open(latest_report) as f:
        data = json.load(f)
    
    print(f"📊 Test Summary Report")
    print(f"   Report: {latest_report.name}")
    print(f"   Generated: {data['timestamp']}")
    print(f"   Duration: {data['duration']:.2f}s")
    print()
    
    summary = data['summary']
    print(f"📈 Overall Results:")
    print(f"   Categories: {summary['total_categories']}")
    print(f"   Tests: {summary['total_tests']}")
    print(f"   ✅ Passed: {summary['total_passed']}")
    print(f"   ❌ Failed: {summary['total_failed']}")
    print(f"   ⏭️ Skipped: {summary['total_skipped']}")
    
    if summary['total_failed'] > 0:
        print("\n🔍 Failed Tests:")
        for result in data['results']:
            failed_tests = [t for t in result['tests'] if t['status'] == 'FAIL']
            if failed_tests:
                print(f"   {result['category'].upper()}:")
                for test in failed_tests:
                    print(f"     ❌ {test['name']}")
                    if 'error' in test:
                        print(f"        Error: {test['error']}")

if __name__ == "__main__":
    generate_summary()