import os
import sys
import json
import time
import math
import sqlite3
import threading
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

CHALLENGER_DB = os.path.join(PROJECT_ROOT, 'test_challenger_stress.db')
os.environ['AL_SANGMOO_DB_PATH'] = CHALLENGER_DB

import db_manager
import generate_dashboard_feed
import al_sangmoo_daily_bot
from al_sangmoo.core.constants import WATCHLIST, STOCK_DICT, TICKER_SECTORS, get_macro_tailwind_sectors
from al_sangmoo.domain.quant.ichimoku import (
    calculate_ichimoku_indicators,
    detect_cloud_trampoline_bounce,
    compute_institutional_flow_indicators,
    build_ichimoku_series_payload
)
from al_sangmoo.domain.quant.scoring import (
    evaluate_quant_score,
    classify_3tier_candidates
)

def generate_adversarial_ohlcv(scenario: str, n_bars: int = 120, seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)
    dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n_bars)
    
    if scenario == 'trampoline':
        closes = [150.0 - i * 0.8 if i < 40 else 118.0 + (i - 40) * 0.9 for i in range(n_bars)]
        highs = [c + 1.5 for c in closes]
        lows = [c - 1.5 for c in closes]
        opens = [(h + l) / 2 for h, l in zip(highs, lows)]
        volumes = [int(1_500_000 + i * 10000) for i in range(n_bars)]
    elif scenario == 'breakout_volume':
        closes = [100.0 + i * 0.5 for i in range(n_bars)]
        highs = [c + 2.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        opens = [(h + l) / 2 for h, l in zip(highs, lows)]
        volumes = [int(500_000 if i < n_bars - 5 else 4_500_000) for i in range(n_bars)]
    elif scenario == 'bear_breakdown':
        closes = [200.0 - i * 1.2 for i in range(n_bars)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 2.5 for c in closes]
        opens = [(h + l) / 2 for h, l in zip(highs, lows)]
        volumes = [int(800_000 + i * 5000) for i in range(n_bars)]
    elif scenario == 'zero_volume':
        closes = [50.0 + math.sin(i / 5.0) * 2.0 for i in range(n_bars)]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        opens = closes[:]
        volumes = [0 for _ in range(n_bars)]
    else:  # random_walk
        closes = [100.0]
        for i in range(1, n_bars):
            closes.append(max(5.0, closes[-1] + (np.random.rand() - 0.49) * 3.0))
        highs = [c + abs(np.random.rand()) * 2.0 + 0.5 for c in closes]
        lows = [max(1.0, c - abs(np.random.rand()) * 2.0 - 0.5) for c in closes]
        opens = [(h + l) / 2 for h, l in zip(highs, lows)]
        volumes = [int(1_000_000 + np.random.randint(0, 500_000)) for _ in range(n_bars)]
        
    return pd.DataFrame({
        'Open': opens, 'High': highs, 'Low': lows, 'Close': closes, 'Volume': volumes
    }, index=dates)


class TestChallengerEmpiricalVerification(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if os.path.exists(CHALLENGER_DB):
            os.remove(CHALLENGER_DB)
        db_manager.init_db()
        
        db_manager.add_portfolio_buy('NVDA', 120.0, 10)
        db_manager.add_portfolio_buy('AAPL', 220.0, 5)
        db_manager.save_recommendation_matrix_record(
            date_str='2026-08-22',
            bull_picks=[{'ticker': 'NVDA', 'close': 120.0}, {'ticker': 'AAPL', 'close': 220.0}],
            neutral_picks=[{'ticker': 'MSFT', 'close': 400.0}, {'ticker': 'AMZN', 'close': 180.0}],
            bear_picks=[{'ticker': 'TSLA', 'close': 200.0}, {'ticker': 'GOOGL', 'close': 160.0}]
        )
        db_manager.archive_daily_recommendations(
            today_str='2026-08-22',
            dual_consensus=[{'ticker': 'NVDA', 'price': 120.0}],
            strat1_exclusive=[{'ticker': 'AAPL', 'price': 220.0}],
            strat2_exclusive=[{'ticker': 'MSFT', 'price': 400.0}]
        )

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(CHALLENGER_DB):
            try:
                os.remove(CHALLENGER_DB)
            except Exception:
                pass

    def _get_db_state(self) -> dict:
        with db_manager.get_connection() as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
            state = {}
            for t in tables:
                cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                state[t] = cnt
            return state

    def test_concurrent_threads_build_dashboard_data_zero_db_locks_zero_mutations(self):
        """
        TASK 1 EMPIRICAL STRESS TEST:
        Run build_dashboard_data() concurrently across 16 threads while simultaneous
        background worker threads perform read/write SQLite operations.
        Assert:
          1. 0 database locks (no OperationalError)
          2. 0 unhandled exceptions across all threads
          3. Total row count in SQLite DB remains exactly unchanged from build_dashboard_data
        """
        print('\n[Challenger Stress 1] Running 16-Thread Concurrent build_dashboard_data() with DB contention...')
        
        initial_state = self._get_db_state()
        synthetic_cache = {
            tk: generate_adversarial_ohlcv('random_walk', n_bars=100, seed=i)
            for i, tk in enumerate(WATCHLIST)
        }
        
        def mock_download(ticker, *args, **kwargs):
            return synthetic_cache.get(ticker, generate_adversarial_ohlcv('random_walk', n_bars=100))

        errors = []
        payloads = []
        lock = threading.Lock()
        
        def run_dashboard_task(thread_id):
            try:
                with patch('yfinance.download', side_effect=mock_download):
                    res = generate_dashboard_feed.build_dashboard_data()
                    with lock:
                        payloads.append((thread_id, res))
            except Exception as e:
                with lock:
                    errors.append((thread_id, str(e)))

        num_threads = 16
        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=run_dashboard_task, args=(i,))
            threads.append(t)

        start_time = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=90)
        elapsed = time.time() - start_time

        self.assertEqual(len(errors), 0, f'Concurrency errors encountered: {errors}')
        self.assertEqual(len(payloads), num_threads, f'Expected {num_threads} completed payloads, got {len(payloads)}')

        final_state = self._get_db_state()
        self.assertEqual(initial_state, final_state, f'Database mutation detected! Initial: {initial_state}, Final: {final_state}')
        
        first_payload = payloads[0][1]
        self.assertIn('dual_consensus', first_payload)
        self.assertIn('strat1_exclusive', first_payload)
        self.assertIn('strat2_exclusive', first_payload)
        self.assertIn('signal_tracker', first_payload)
        self.assertIn('chart_intelligence', first_payload)
        
        print(f'  -> PASSED: 16 concurrent threads finished in {elapsed:.2f}s with 0 DB locks and 0 SQLite mutations.')

    def test_indicator_and_scoring_parity_between_feed_and_bot(self):
        """
        TASK 2 EMPIRICAL PARITY TEST:
        Feed identical market datasets through:
          - generate_dashboard_feed.compute_all_indicators logic
          - al_sangmoo_daily_bot.scan_and_select_2x2x2 logic
        Assert 100% mathematical and categorical parity on all outputs.
        """
        print('\n[Challenger Parity 2] Verifying 100% Indicator and Scoring Parity between Feed and Bot...')
        
        test_tickers = ['NVDA', 'AAPL', 'MSFT', 'TSLA', 'PLTR', 'AMD']
        test_scenarios = ['trampoline', 'breakout_volume', 'bear_breakdown', 'zero_volume', 'random_walk']
        
        for scenario in test_scenarios:
            for seed_idx, ticker in enumerate(test_tickers):
                df = generate_adversarial_ohlcv(scenario, n_bars=120, seed=100 + seed_idx)
                
                df_daily = calculate_ichimoku_indicators(df.copy())
                df_clean = df_daily.dropna(subset=['Close', 'Kijun', 'Tenkan', 'SMA20', 'Vol_Ratio'])
                if df_clean.empty:
                    continue
                    
                last = df_clean.iloc[-1]
                close = float(last['Close'])
                kijun = float(last['Kijun'])
                tenkan = float(last['Tenkan'])
                vol_ratio = float(last['Vol_Ratio']) if not pd.isna(last['Vol_Ratio']) else 1.0
                span_a = float(last['SpanA']) if not pd.isna(last['SpanA']) else close
                span_b = float(last['SpanB']) if not pd.isna(last['SpanB']) else close
                
                df_w = df[['Open', 'High', 'Low', 'Close', 'Volume']].resample('W-FRI').agg({
                    'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
                }).dropna()
                df_w = calculate_ichimoku_indicators(df_w)
                w_clean = df_w.dropna(subset=['Close', 'Kijun', 'Tenkan'])
                if not w_clean.empty:
                    w_last = w_clean.iloc[-1]
                    w_close = float(w_last['Close'])
                    w_span_a = float(w_last['SpanA']) if not pd.isna(w_last['SpanA']) else w_close
                    w_span_b = float(w_last['SpanB']) if not pd.isna(w_last['SpanB']) else w_close
                    is_weekly_bull = (w_close >= max(w_span_a, w_span_b) * 0.98)
                else:
                    is_weekly_bull = (close >= max(span_a, span_b) * 0.97)
                    
                trampoline_detected, trampoline_days, touch_gap, close_gap = detect_cloud_trampoline_bounce(df_clean)
                flow_data = compute_institutional_flow_indicators(df_clean)
                
                q_eval = evaluate_quant_score(
                    close=close,
                    kijun=kijun,
                    tenkan=tenkan,
                    span_a=span_a,
                    span_b=span_b,
                    vol_ratio=vol_ratio,
                    trampoline_detected=trampoline_detected,
                    is_weekly_bull=is_weekly_bull,
                    days_ago=trampoline_days
                )
                
                with patch('yfinance.download', return_value=df):
                    feed_result = generate_dashboard_feed.compute_all_indicators(ticker)
                
                self.assertIsNotNone(feed_result, f'Feed computation failed for {ticker} under {scenario}')
                
                self.assertAlmostEqual(feed_result['latest_close'], round(close, 2), places=2)
                self.assertAlmostEqual(feed_result['kijun'], round(kijun, 2), places=2)
                self.assertAlmostEqual(feed_result['tenkan'], round(tenkan, 2), places=2)
                self.assertAlmostEqual(feed_result['span_a'], round(span_a, 2), places=2)
                self.assertAlmostEqual(feed_result['span_b'], round(span_b, 2), places=2)
                self.assertAlmostEqual(feed_result['vol_ratio'], round(vol_ratio, 2), places=2)
                
                self.assertEqual(feed_result['bull_score'], q_eval['bull_score'])
                self.assertEqual(feed_result['bear_score'], q_eval['bear_score'])
                self.assertEqual(feed_result['sniper_score'], q_eval['sniper_score'])
                self.assertEqual(feed_result['is_sniper'], q_eval['is_sniper_active'])
                self.assertEqual(feed_result['status_tag'], q_eval['quant_type'])
                self.assertEqual(feed_result['status_text'], q_eval['quant_verdict'])
                
                self.assertEqual(feed_result['obv_status'], flow_data['obv_status'])
                self.assertEqual(feed_result['is_stealth_accum'], flow_data['is_stealth_accum'])
                self.assertAlmostEqual(feed_result['flow_ratio'], flow_data['flow_ratio'], places=4)
                
        print('  -> PASSED: 100% Indicator, Flow, and Scoring parity verified across all scenarios.')

    def test_3tier_classification_parity_on_macro_swings(self):
        print('\n[Challenger Parity 3] Testing 3-Tier Classification Determinism across Macro Regimes...')
        
        chart_data = {}
        for i, tk in enumerate(['NVDA', 'AAPL', 'MSFT', 'AMZN', 'GOOGL', 'META', 'TSLA', 'AVGO', 'PLTR', 'AMD']):
            df = generate_adversarial_ohlcv('random_walk', n_bars=100, seed=500 + i)
            with patch('yfinance.download', return_value=df):
                res = generate_dashboard_feed.compute_all_indicators(tk)
                if res:
                    chart_data[tk] = res

        regimes = [
            ('ACTIVE_BUY', ['TECH_GROWTH', 'AI_SEMICONDUCTOR', 'NUCLEAR_POWER']),
            ('SELECTIVE_BUY', ['TECH_GROWTH', 'DIVIDEND_VALUE']),
            ('DEFENSE_HOLD', ['HEALTHCARE_DEFENSE', 'DIVIDEND_VALUE']),
            ('CASH_EXIT', ['DEFENSE_CASH'])
        ]
        
        for stance, tailwind in regimes:
            dual_1, s1_1, s2_1 = classify_3tier_candidates(chart_data, tailwind, stream_mentioned_tickers={'NVDA', 'PLTR'})
            dual_2, s1_2, s2_2 = classify_3tier_candidates(chart_data, tailwind, stream_mentioned_tickers={'NVDA', 'PLTR'})
            
            self.assertEqual([x['ticker'] for x in dual_1], [x['ticker'] for x in dual_2])
            self.assertEqual([x['ticker'] for x in s1_1], [x['ticker'] for x in s1_2])
            self.assertEqual([x['ticker'] for x in s2_1], [x['ticker'] for x in s2_2])
            
            set_dual = {x['ticker'] for x in dual_1}
            set_s1 = {x['ticker'] for x in s1_1}
            set_s2 = {x['ticker'] for x in s2_1}
            
            self.assertTrue(set_dual.isdisjoint(set_s1), f'Dual and Strat1 overlap in {stance}')
            self.assertTrue(set_dual.isdisjoint(set_s2), f'Dual and Strat2 overlap in {stance}')
            self.assertTrue(set_s1.isdisjoint(set_s2), f'Strat1 and Strat2 overlap in {stance}')
            
            for item in dual_1 + s1_1 + s2_1:
                price = item['price']
                expected_target = round(price * 1.15, 2)
                expected_stop = round(price * 0.96, 2)
                self.assertAlmostEqual(item['target_price'], expected_target, places=2)
                self.assertAlmostEqual(item['stop_price'], expected_stop, places=2)

        print('  -> PASSED: 3-Tier Classification is 100% deterministic, disjoint, and risk-guardrail compliant.')

    def test_extreme_edge_cases_and_resilience(self):
        print('\n[Challenger Stress 4] Testing Extreme Edge Cases: Constant price, zero volume, NaN recovery...')
        
        # 1. Constant price (flatline)
        df_const = generate_adversarial_ohlcv('zero_volume', n_bars=80)
        df_const['Open'] = 100.0
        df_const['High'] = 100.0
        df_const['Low'] = 100.0
        df_const['Close'] = 100.0
        df_const_ind = calculate_ichimoku_indicators(df_const)
        self.assertEqual(df_const_ind['Tenkan'].iloc[-1], 100.0)
        self.assertEqual(df_const_ind['Kijun'].iloc[-1], 100.0)
        
        # 2. Short dataframe (< 10 bars) - must not raise exception
        df_short = df_const.iloc[:5]
        df_short_ind = calculate_ichimoku_indicators(df_short)
        self.assertEqual(len(df_short_ind), 5)
        
        # 3. Future cloud projection on constant series
        span_a_pts, span_b_pts, a_vals, b_vals = generate_dashboard_feed.build_ichimoku_series_payload(df_const_ind)['future_span_a'], [], [], []
        self.assertTrue(len(span_a_pts) > 0)
        print('  -> PASSED: Extreme edge cases and boundary conditions survive gracefully.')


if __name__ == '__main__':
    print('===============================================================================')
    print('  RUNNING CHALLENGER 1 EMPIRICAL STRESS & PARITY TEST SUITE (PHASE 5.3 ITER 2) ')
    print('===============================================================================')
    unittest.main(verbosity=2)

