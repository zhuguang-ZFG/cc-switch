#!/usr/bin/env python3
"""
Monitor muyuan-gongyi (ch173) embedding endpoint stability.
Tests all 4 embedding models and reports success/failure rates.
"""

import subprocess
import json
import time
from datetime import datetime
from pathlib import Path

EMBEDDING_MODELS = [
    'mistral-embed',
    'codestral-embed', 
    'mistral-embed-2312',
    'codestral-embed-2505'
]

NEWAPI_URL = 'http://127.0.0.1:3002/v1/embeddings'
API_KEY = 'sk-XgEtMUd1JkqJOXcqxxCUR52zOi7MSeC76Lf5NvDQl2tirV0y'

def test_embedding(model: str) -> dict:
    """Test a single embedding model."""
    start = time.time()
    try:
        result = subprocess.run(
            ['curl', '-s', '-m', '10', '-X', 'POST', NEWAPI_URL,
             '-H', f'Authorization: Bearer {API_KEY}',
             '-H', 'Content-Type: application/json',
             '-d', json.dumps({'model': model, 'input': 'Test stability check'})],
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
                'error': error[:80]
            }
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        return {
            'model': model,
            'status': 'ERR',
            'latency_ms': elapsed,
            'error': str(e)[:80]
        }

def main():
    """Run stability check for all embedding models."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] Embedding Stability Check")
    print("=" * 60)
    
    results = []
    for model in EMBEDDING_MODELS:
        result = test_embedding(model)
        results.append(result)
        
        status_icon = '[OK]' if result['status'] == 'OK' else '[FAIL]'
        if result['status'] == 'OK':
            print(f"{status_icon} {model}: {result['latency_ms']}ms, {result['dimensions']}d")
        else:
            print(f"{status_icon} {model}: {result['latency_ms']}ms - {result.get('error', 'N/A')}")
    
    # Summary
    ok_count = sum(1 for r in results if r['status'] == 'OK')
    print("\n" + "=" * 60)
    print(f"Summary: {ok_count}/{len(results)} models OK")
    
    if ok_count == len(results):
        avg_latency = sum(r['latency_ms'] for r in results) // len(results)
        print(f"Average latency: {avg_latency}ms")
        print("Status: ALL STABLE")
    else:
        print("Status: DEGRADED")
    
    return ok_count == len(results)

if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)
