import ast
import os
import sys

base_dir = r"d:\코딩\Playground\al_sangmoo_project"
files_to_check = [
    os.path.join(base_dir, "generate_dashboard_feed.py"),
    os.path.join(base_dir, "al_sangmoo_daily_bot.py"),
    os.path.join(base_dir, "youtube_stream_scanner.py"),
    os.path.join(base_dir, "server.py"),
]

print("=" * 60)
print("AST ANALYSIS: DUPLICATE MATH, ROLLING CALLS, & IMPORTS")
print("=" * 60)

for fpath in files_to_check:
    fname = os.path.basename(fpath)
    if not os.path.exists(fpath):
        print(f"File missing: {fpath}")
        continue
    with open(fpath, "r", encoding="utf-8") as f:
        content = f.read()
    tree = ast.parse(content, filename=fname)
    
    # 1. Check duplicate function definitions
    func_names = [node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    print(f"\n--- File: {fname} ---")
    duplicate_candidates = [
        "build_ichimoku_series",
        "calculate_indicators",
        "calculate_ichimoku",
        "compute_ichimoku",
        "calculate_obv",
        "evaluate_macro_stance",
        "calculate_bull_score",
        "classify_tiers"
    ]
    found_dups = [d for d in duplicate_candidates if d in func_names]
    if found_dups:
        print(f"[AST DUPLICATE DEF] Found candidate duplicate functions: {found_dups}")
    else:
        print("[AST DUPLICATE DEF PASS] Zero duplicate domain indicator functions defined.")

    # 2. Check for domain imports
    domain_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "domain" in alias.name or "quant" in alias.name:
                    domain_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if "domain" in mod or "quant" in mod or "ichimoku" in mod or "scoring" in mod or "macro" in mod:
                for alias in node.names:
                    domain_imports.append(f"{mod}.{alias.name}")
    print(f"[DOMAIN IMPORTS] {domain_imports}")

    # 3. Check for inline .rolling(9), .rolling(26), .rolling(52), .rolling(20), .rolling(50), .rolling(200)
    rolling_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "rolling":
                # Get the argument value if constant
                arg_vals = []
                for arg in node.args:
                    if isinstance(arg, ast.Constant):
                        arg_vals.append(arg.value)
                    elif isinstance(arg, ast.Num):
                        arg_vals.append(arg.n)
                    else:
                        arg_vals.append(ast.dump(arg))
                rolling_calls.append((node.lineno, arg_vals))
    if rolling_calls:
        print(f"[INLINE ROLLING CALLS] {rolling_calls}")
    else:
        print("[INLINE ROLLING CALLS] Zero .rolling() calls detected.")
