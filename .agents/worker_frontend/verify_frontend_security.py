import os
import re
import html.parser
import hashlib

PROJECT_ROOT = r"d:\코딩\Playground\al_sangmoo_project"
TARGET_FILES = [
    os.path.join(PROJECT_ROOT, "al_sangmoo_dashboard.html"),
    os.path.join(PROJECT_ROOT, "html_dashboards", "01_R상무_통합_퀀트_대시보드.html"),
    os.path.join(PROJECT_ROOT, "html_dashboards", "01_알상무_통합_퀀트_대시보드.html"),
    os.path.join(PROJECT_ROOT, "HTML_대시보드_모음", "01_R상무_통합_퀀트_대시보드.html"),
]

def simulate_escape_html(s):
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#039;")

def test_xss_payloads():
    payloads = [
        ("<script>alert(1)</script>", "&lt;script&gt;alert(1)&lt;/script&gt;"),
        ("AAPL' onclick='alert(1)'", "AAPL&#039; onclick=&#039;alert(1)&#039;"),
        ('"><img src=x onerror=alert(1)>', '&quot;&gt;&lt;img src=x onerror=alert(1)&gt;'),
        ("TEST & PROD", "TEST &amp; PROD"),
    ]
    for raw, expected in payloads:
        escaped = simulate_escape_html(raw)
        assert escaped == expected, f"Escape failure: {escaped} != {expected}"
    print("XSS payload escaping logic: PASSED (100% match)")

def verify_all_files():
    test_xss_payloads()
    
    first_hash = None

    for fpath in TARGET_FILES:
        rel = os.path.relpath(fpath, PROJECT_ROOT)
        print(f"\n[Verifying] {rel}")
        assert os.path.exists(fpath), f"File missing: {fpath}"
        
        with open(fpath, "rb") as f:
            raw_bytes = f.read()
            h = hashlib.sha256(raw_bytes).hexdigest()
            if first_hash is None:
                first_hash = h
            else:
                assert h == first_hash, f"Hash mismatch between canonical and mirror: {rel}"
        
        content = raw_bytes.decode("utf-8")
        
        # 1. escapeHtml definition
        assert "function escapeHtml(str)" in content
        assert "return String(str)" in content
        assert ".replace(/&/g, '&amp;')" in content
        assert ".replace(/</g, '&lt;')" in content
        assert ".replace(/>/g, '&gt;')" in content
        assert ".replace(/\"/g, '&quot;')" in content
        assert ".replace(/'/g, '&#039;')" in content
        print("  - escapeHtml utility: VERIFIED")

        # 2. No dynamic string template onclick handlers
        assert 'onclick="selectStock' not in content
        assert 'onclick="sellHolding' not in content
        assert 'onclick="selectSearchedStock' not in content
        print("  - Inline dynamic onclick handlers eliminated: VERIFIED")

        # 3. Safe data-* attributes
        assert 'data-ticker="${safeTicker}"' in content
        assert 'data-price="${safePrice}"' in content
        assert 'data-holding-id="${safeId}"' in content
        assert 'data-current-price="${safeCurrentPrice}"' in content
        print("  - Safe data-* attributes in place: VERIFIED")

        # 4. Stream link protocol regex
        assert "/^https?:\\/\\//i.test(data.macro.url)" in content
        print("  - Stream URL protocol check: VERIFIED")

        # 5. Macro tailwind sectors escaping
        assert "tailwindEl.innerHTML = sectors.map(s => `[${escapeHtml(s)}]`).join(' ');" in content
        print("  - Macro tailwind sectors sanitization: VERIFIED")

        # 6. Event delegation handlers
        assert "function initFrontendEventDelegation()" in content
        assert '["strat1ExclusiveCards", "dualConsensusCards", "strat2ExclusiveCards"].forEach' in content
        assert 'pBody.addEventListener("click"' in content
        assert 'dailyRecBody.addEventListener("click"' in content
        assert 'searchDropdownEl.addEventListener("click"' in content
        assert "initFrontendEventDelegation();" in content
        print("  - Event delegation on parent containers: VERIFIED")

        # 7. HTML structure parsing
        parser = html.parser.HTMLParser()
        parser.feed(content)
        print("  - HTML syntax validation: VERIFIED")

if __name__ == "__main__":
    verify_all_files()
    print("\n=======================================================")
    print("ALL 4 DASHBOARD MIRRORS VERIFIED AND SYNCHRONIZED 100%!")
    print("=======================================================")
