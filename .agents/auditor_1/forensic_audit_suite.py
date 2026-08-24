# Forensic Integrity Audit Suite
import ast
import os
import sys
import re
import json
import asyncio
import hashlib
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

print('=' * 80)
print('FORENSIC INTEGRITY AUDIT SUITE: AL-SANGMOO QUANT TRADING PLATFORM')
print('=' * 80)

# -------------------------------------------------------------
# PHASE 1: STATIC & AST CODE INTEGRITY ANALYSIS
# -------------------------------------------------------------
print('\n[PHASE 1] STATIC & AST CODE INTEGRITY ANALYSIS')

target_files = {
    'server.py': os.path.join(PROJECT_ROOT, 'server.py'),
    'hub.py': os.path.join(PROJECT_ROOT, 'al_sangmoo', 'api', 'hub.py'),
    'persistence.py': os.path.join(PROJECT_ROOT, 'al_sangmoo', 'infrastructure', 'persistence.py'),
    'youtube_stream_scanner.py': os.path.join(PROJECT_ROOT, 'youtube_stream_scanner.py'),
    'test_phase5_1_security.py': os.path.join(PROJECT_ROOT, 'tools_and_tests', 'test_phase5_1_security.py'),
}

findings = []

for name, fpath in target_files.items():
    print(f'Checking {name} ({fpath})...')
    assert os.path.exists(fpath), f'Missing file: {fpath}'
    with open(fpath, 'r', encoding='utf-8') as f:
        src = f.read()

    tree = ast.parse(src, filename=fpath)
    
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test_str = ast.unparse(node.test) if hasattr(ast, 'unparse') else ''
            if any(k in test_str.lower() for k in ['test_mode', 'mock', 'is_test', 'testing', 'bypass']):
                findings.append(f'Suspicious condition in {name}:{node.lineno}: if {test_str}')
                print(f'  [FLAG] Suspicious if condition: {test_str} (line {node.lineno})')
        
        if isinstance(node, ast.FunctionDef):
            if len(node.body) == 1:
                stmt = node.body[0]
                if isinstance(stmt, ast.Pass) and not name.startswith('test_'):
                    findings.append(f'Empty function body (pass) in {name}:{node.lineno} def {node.name}')
                    print(f'  [FLAG] Empty function: {node.name} at line {node.lineno}')

print(f'Phase 1 AST findings count: {len(findings)}')

# -------------------------------------------------------------
# PHASE 2: DETAILED REQUIREMENT AUDIT (R1 to R5)
# -------------------------------------------------------------
print('\n[PHASE 2] REQUIREMENT IMPLEMENTATION AUDIT')

# R1. Stored & DOM XSS (SEC-V01)
print('\n[R1 Audit: Stored & DOM XSS]')
import server
import al_sangmoo.infrastructure.persistence as persistence

assert hasattr(server, 'REASON_REGEX'), 'server.py lacks REASON_REGEX'
assert hasattr(persistence, 'REASON_REGEX'), 'persistence.py lacks REASON_REGEX'
assert hasattr(server, 'SellOrder'), 'server.py lacks SellOrder'
print('  - Regex pattern in server.py:', server.REASON_REGEX.pattern)
print('  - Regex pattern in persistence.py:', persistence.REASON_REGEX.pattern)

# R2. CORS Whitelisting & Origin Validation (SEC-V02, SEC-V04)
print('\n[R2 Audit: CORS Whitelisting & Origin Validation]')
assert hasattr(server, 'ALLOWED_ORIGINS'), 'server.py lacks ALLOWED_ORIGINS'
print('  - ALLOWED_ORIGINS:', server.ALLOWED_ORIGINS)
assert '*' not in server.ALLOWED_ORIGINS, 'Wildcard * found in ALLOWED_ORIGINS!'
assert len(server.ALLOWED_ORIGINS) == 4, f'Expected 4 origins, found {len(server.ALLOWED_ORIGINS)}'

import al_sangmoo.api.hub as hub_mod
assert hasattr(hub_mod, 'MAX_CONNECTIONS'), 'hub.py lacks MAX_CONNECTIONS'
print('  - MAX_CONNECTIONS:', hub_mod.MAX_CONNECTIONS)
assert hub_mod.MAX_CONNECTIONS == 50, f'MAX_CONNECTIONS is {hub_mod.MAX_CONNECTIONS}, expected 50'

# R3. Path Traversal & Subprocess Argument Hardening (SEC-V05)
print('\n[R3 Audit: Path Traversal & Subprocess Argument Hardening]')
import youtube_stream_scanner as yt_scanner
assert hasattr(yt_scanner, 'YOUTUBE_ID_REGEX'), 'youtube_stream_scanner.py lacks YOUTUBE_ID_REGEX'
assert hasattr(yt_scanner, 'validate_youtube_id'), 'youtube_stream_scanner.py lacks validate_youtube_id'
assert hasattr(yt_scanner, 'get_safe_vtt_path'), 'youtube_stream_scanner.py lacks get_safe_vtt_path'
print('  - YOUTUBE_ID_REGEX pattern:', yt_scanner.YOUTUBE_ID_REGEX.pattern)

# R4. Pydantic API Input Validation & Global Error Sanitization (SEC-V07, SEC-V08)
print('\n[R4 Audit: Pydantic Input Validation & 500 Sanitization]')
assert hasattr(server, 'BuyOrder'), 'server.py lacks BuyOrder'
assert hasattr(server, 'TICKER_REGEX'), 'server.py lacks TICKER_REGEX'
assert hasattr(server, 'DATE_REGEX'), 'server.py lacks DATE_REGEX'
print('  - TICKER_REGEX:', server.TICKER_REGEX.pattern)
print('  - DATE_REGEX:', server.DATE_REGEX.pattern)

# R5. OWASP Security Response Headers (SEC-V10)
print('\n[R5 Audit: OWASP Response Headers]')
with open(os.path.join(PROJECT_ROOT, 'server.py'), 'r', encoding='utf-8') as f:
    server_code = f.read()

required_headers = [
    'X-Content-Type-Options',
    'X-Frame-Options',
    'Referrer-Policy',
    'X-XSS-Protection'
]
for h in required_headers:
    assert h in server_code, f'Missing header {h} in server.py middleware'
    print(f'  - Header {h} configured in server.py')

# Dashboard HTML Mirrors Audit
print('\n[HTML Mirrors Audit]')
html_files = [
    os.path.join(PROJECT_ROOT, 'al_sangmoo_dashboard.html'),
    os.path.join(PROJECT_ROOT, 'html_dashboards', '01_R상무_통합_퀀트_대시보드.html'),
    os.path.join(PROJECT_ROOT, 'html_dashboards', '01_알상무_통합_퀀트_대시보드.html'),
    os.path.join(PROJECT_ROOT, 'HTML_대시보드_모음', '01_R상무_통합_퀀트_대시보드.html')
]

hashes = []
for hf in html_files:
    assert os.path.exists(hf), f'Missing HTML file {hf}'
    with open(hf, 'r', encoding='utf-8') as f:
        c = f.read()
    assert 'function escapeHtml' in c, f'Missing escapeHtml in {hf}'
    assert 'initFrontendEventDelegation' in c, f'Missing initFrontendEventDelegation in {hf}'
    h_val = hashlib.sha256(c.encode('utf-8')).hexdigest()
    hashes.append((os.path.basename(hf), h_val, len(c)))
    print(f'  - {os.path.basename(hf)}: size={len(c)}, sha256={h_val[:16]}...')

assert len(set(h[1] for h in hashes)) == 1, 'HTML mirrors are not identical in content!'
print('  - All 4 HTML mirrors are 100% synchronized and identical.')

print('\nPhase 2 requirements audit successfully verified.')
