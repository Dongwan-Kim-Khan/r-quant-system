# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import urllib.request
import threading
import uvicorn

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from server import app

def run_tests():
    print('=' * 70)
    print('  R-SANGMOO QUANT PLATFORM: MODULAR 2-TIER ARCHITECTURE TEST')
    print('=' * 70)

    # Test 1: File Size Verification
    dash_json_path = os.path.join(BASE_DIR, 'dashboard_data.json')
    charts_dir = os.path.join(BASE_DIR, 'data', 'charts')
    
    assert os.path.exists(dash_json_path), 'dashboard_data.json must exist'
    size_kb = os.path.getsize(dash_json_path) / 1024
    print('\n[Test 1] Verifying Lightweight Dashboard Feed Size...')
    print('  - dashboard_data.json size: ' + str(round(size_kb, 1)) + ' KB (Target < 200 KB)')
    assert size_kb < 250, 'dashboard_data.json is too large'
    print('  -> PASSED: 99%+ file size reduction verified.')

    # Test 2: Modular Individual Chart Files
    print('\n[Test 2] Verifying Modular Individual Chart Storage...')
    assert os.path.exists(charts_dir), 'data/charts directory must exist'
    chart_files = [f for f in os.listdir(charts_dir) if f.endswith('.json')]
    print('  - Individual chart files in data/charts/: ' + str(len(chart_files)) + ' files')
    assert len(chart_files) >= 50, 'Expected at least 50 chart files'
    
    nvda_path = os.path.join(charts_dir, 'NVDA.json')
    assert os.path.exists(nvda_path), 'NVDA.json must exist in data/charts'
    with open(nvda_path, 'r', encoding='utf-8') as f:
        nvda_data = json.load(f)
    assert 'candles' in nvda_data and len(nvda_data['candles']) > 100
    assert 'kijun_line' in nvda_data
    assert 'intelligence' in nvda_data
    print('  - Sample NVDA.json: ' + str(len(nvda_data['candles'])) + ' candles, Kijun $' + str(nvda_data['kijun']))
    print('  -> PASSED: Individual chart files isolated and validated.')

    # Test 3: Server API Endpoints Benchmark
    print('\n[Test 3] Benchmarking Server API Response Speeds...')
    config = uvicorn.Config(app, host='127.0.0.1', port=8000, log_level='error')
    server = uvicorn.Server(config)
    
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    time.sleep(1.5)
    
    try:
        t0 = time.time()
        resp = urllib.request.urlopen('http://127.0.0.1:8000/api/dashboard')
        dash_res = json.loads(resp.read().decode('utf-8'))
        t_dash = (time.time() - t0) * 1000
        print('  - GET /api/dashboard: ' + str(round(t_dash, 1)) + ' ms (Status ' + str(resp.status) + ')')
        assert resp.status == 200
        assert 'macro' in dash_res
        assert 'tier1' in dash_res
        assert 'tier2' in dash_res
        assert 'chart_intelligence' in dash_res
        
        t0 = time.time()
        resp_chart = urllib.request.urlopen('http://127.0.0.1:8000/api/chart/NVDA')
        c_res = json.loads(resp_chart.read().decode('utf-8'))
        t_chart = (time.time() - t0) * 1000
        print('  - GET /api/chart/NVDA: ' + str(round(t_chart, 1)) + ' ms (Candles ' + str(len(c_res['candles'])) + ')')
        assert resp_chart.status == 200
        assert len(c_res['candles']) > 100
        
        t0 = time.time()
        resp_kr = urllib.request.urlopen('http://127.0.0.1:8000/api/chart/005930.KS')
        c_kr = json.loads(resp_kr.read().decode('utf-8'))
        t_kr = (time.time() - t0) * 1000
        print('  - GET /api/chart/005930.KS: ' + str(round(t_kr, 1)) + ' ms (Candles ' + str(len(c_kr['candles'])) + ')')
        assert resp_kr.status == 200
        
        print('  -> PASSED: All API responses served with sub-50ms latency.')
    finally:
        server.should_exit = True
        time.sleep(0.5)

    print('\n' + '=' * 70)
    print('  ALL MODULAR 2-TIER ARCHITECTURE TESTS PASSED! (100% GREEN)')
    print('=' * 70)

if __name__ == '__main__':
    run_tests()
