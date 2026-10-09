#!/usr/bin/env python3
"""
Monitor ch173 (muyuan-gongyi) channel availability with alert thresholds.

This script monitors the embedding model channel and triggers alerts when:
- Any embedding model fails
- Average latency exceeds threshold
- Channel status changes

Usage:
    python3 scripts/ops/monitor-ch173-availability.py
    
Exit codes:
    0: All checks passed
    1: Warning threshold exceeded
    2: Critical threshold exceeded
"""

import subprocess
import json
import time
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

# Configuration
CHANNEL_ID = 173
CHANNEL_NAME = "muyuan-gongyi"
NEWAPI_DB = Path.home() / '.new-api-local' / 'new-api.db'
NEWAPI_URL = 'http://127.0.0.1:3002/v1/embeddings'
API_KEY = 'sk-XgEtMUd1JkqJOXcqxxCUR52zOi7MSeC76Lf5NvDQl2tirV0y'
LOG_FILE = Path.home() / '.omp' / 'logs' / 'ch173-monitor.log'

EMBEDDING_MODELS = [
    'mistral-embed-2312',  # Primary (fastest)
    'codestral-embed-2505',
    'mistral-embed',
    'codestral-embed',
]

# Alert thresholds
LATENCY_WARNING_MS = 1500  # 1.5 seconds
LATENCY_CRITICAL_MS = 3000  # 3 seconds
MIN_MODELS_REQUIRED = 2  # At least 2 models must be available

def log_message(message: str, level: str = "INFO"):
    """Log message to file and stdout."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_line = f"[{timestamp}] [{level}] {message}"
    print(log_line)
    
    # Ensure log directory exists
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(log_line + '\n')

def test_embedding_model(model: str) -> Dict:
    """Test a single embedding model."""
    start = time.time()
    try:
        result = subprocess.run(
            ['curl', '-s', '-m', '10', '-X', 'POST', NEWAPI_URL,
             '-H', f'Authorization: Bearer {API_KEY}',
             '-H', 'Content-Type: application/json',
             '-d', json.dumps({'model': model, 'input': 'Monitor health check'})],
            capture_output=True, text=True, timeout=15, encoding='utf-8', errors='ignore'
        )
        elapsed = int((time.time() - start) * 1000)
        d = json.loads(result.stdout)
        
        if d.get('data'):
            dimensions = len(d['data'][0].get('embedding', []))
            return {
                'model': model,
                'status': 'OK',
                'latency_ms': elapsed,
                'dimensions': dimensions
            }
        else:
            error = d.get('error', {}).get('message', 'Unknown error')
            return {
                'model': model,
                'status': 'FAIL',
                'latency_ms': elapsed,
                'error': error[:100]
            }
    except subprocess.TimeoutExpired:
        elapsed = int((time.time() - start) * 1000)
        return {
            'model': model,
            'status': 'TIMEOUT',
            'latency_ms': elapsed,
            'error': 'Request timeout (>10s)'
        }
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        return {
            'model': model,
            'status': 'ERR',
            'latency_ms': elapsed,
            'error': str(e)[:100]
        }

def check_channel_status() -> Tuple[bool, str]:
    """Check ch173 channel status in NewAPI database."""
    try:
        import sqlite3
        conn = sqlite3.connect(str(NEWAPI_DB))
        cur = conn.cursor()
        cur.execute('SELECT id, name, status FROM channels WHERE id = ?', (CHANNEL_ID,))
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return False, f"Channel {CHANNEL_ID} not found in database"
        
        status = row[2]
        if status == 1:
            return True, f"ch{row[0]} ({row[1]}): enabled"
        else:
            return False, f"ch{row[0]} ({row[1]}): disabled (status={status})"
    except Exception as e:
        return False, f"Database error: {str(e)}"

def send_alert(subject: str, message: str, level: str = "CRITICAL"):
    """Send alert notification."""
    # Log the alert
    log_message(f"ALERT [{level}]: {subject} - {message}", level)
    
    # TODO: Implement actual alert mechanisms
    # Examples:
    # - Email: send_email(subject, message)
    # - Slack: post_to_slack(subject, message)
    # - Webhook: trigger_webhook(subject, message)
    # - SMS: send_sms(message)
    
    # For now, just log to a separate alert file
    alert_file = Path.home() / '.omp' / 'logs' / 'embedding-alerts.log'
    alert_file.parent.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(alert_file, 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] [{level}] {subject}\n")
        f.write(f"  {message}\n\n")

def main():
    """Main monitoring function."""
    log_message("=" * 60)
    log_message(f"Starting ch{CHANNEL_ID} ({CHANNEL_NAME}) availability check")
    
    # Check 1: Channel status
    channel_ok, channel_msg = check_channel_status()
    if not channel_ok:
        log_message(channel_msg, "ERROR")
        send_alert(
            "Channel Disabled",
            f"ch{CHANNEL_ID} is disabled in NewAPI database. {channel_msg}",
            "CRITICAL"
        )
        return 2  # Critical
    
    log_message(channel_msg)
    
    # Check 2: Test all embedding models
    results = []
    for model in EMBEDDING_MODELS:
        result = test_embedding_model(model)
        results.append(result)
        
        if result['status'] == 'OK':
            log_message(f"[OK] {result['model']}: {result['latency_ms']}ms, {result['dimensions']}d")
        else:
            log_message(f"[{result['status']}] {result['model']}: {result['latency_ms']}ms - {result.get('error', 'N/A')}", "ERROR")
    
    # Check 3: Analyze results
    ok_models = [r for r in results if r['status'] == 'OK']
    ok_count = len(ok_models)
    
    if ok_count == 0:
        log_message("CRITICAL: All embedding models failed!", "ERROR")
        send_alert(
            "All Models Failed",
            f"All {len(EMBEDDING_MODELS)} embedding models are unavailable. Semantic search is completely down.",
            "CRITICAL"
        )
        return 2  # Critical
    
    if ok_count < MIN_MODELS_REQUIRED:
        log_message(f"WARNING: Only {ok_count}/{len(EMBEDDING_MODELS)} models available", "WARNING")
        send_alert(
            "Low Model Availability",
            f"Only {ok_count}/{len(EMBEDDING_MODELS)} embedding models are available. Minimum required: {MIN_MODELS_REQUIRED}",
            "WARNING"
        )
    
    # Check 4: Latency analysis
    if ok_models:
        avg_latency = sum(r['latency_ms'] for r in ok_models) // len(ok_models)
        max_latency = max(r['latency_ms'] for r in ok_models)
        
        log_message(f"Average latency: {avg_latency}ms, Max: {max_latency}ms")
        
        if max_latency > LATENCY_CRITICAL_MS:
            log_message(f"CRITICAL: Max latency {max_latency}ms exceeds threshold {LATENCY_CRITICAL_MS}ms", "ERROR")
            send_alert(
                "High Latency",
                f"Max latency {max_latency}ms exceeds critical threshold {LATENCY_CRITICAL_MS}ms. Average: {avg_latency}ms",
                "CRITICAL"
            )
            return 2  # Critical
        
        if avg_latency > LATENCY_WARNING_MS:
            log_message(f"WARNING: Average latency {avg_latency}ms exceeds threshold {LATENCY_WARNING_MS}ms", "WARNING")
            send_alert(
                "Elevated Latency",
                f"Average latency {avg_latency}ms exceeds warning threshold {LATENCY_WARNING_MS}ms",
                "WARNING"
            )
            return 1  # Warning
    
    # All checks passed
    log_message(f"Status: HEALTHY ({ok_count}/{len(EMBEDDING_MODELS)} models OK)")
    return 0  # OK

if __name__ == '__main__':
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        log_message("Monitor interrupted by user", "WARNING")
        sys.exit(1)
    except Exception as e:
        log_message(f"Unexpected error: {str(e)}", "ERROR")
        send_alert("Monitor Error", f"Monitoring script failed: {str(e)}", "CRITICAL")
        sys.exit(2)
