"""
Al-Sangmoo Quantitative Domain Layer (Single Source of Truth).
"""
from al_sangmoo.domain.quant.ichimoku import (
    calculate_ichimoku_indicators,
    project_future_cloud,
    detect_cloud_trampoline_bounce,
    compute_institutional_flow_indicators,
    build_ichimoku_series_payload,
)
from al_sangmoo.domain.quant.scoring import (
    QuantIndicators,
    WeeklyTrendContext,
    InstitutionalFlowContext,
    TrampolineBounceContext,
    QuantScoreBreakdown,
    TierClassification,
    calculate_canonical_bull_score,
    calculate_canonical_sniper_score,
    calculate_canonical_bear_score,
    evaluate_quant_score,
    classify_quant_tier,
    classify_3tier_candidates,
)
from al_sangmoo.domain.quant.macro import (
    evaluate_macro_stance,
    calculate_msi_regime,
)

__all__ = [
    "calculate_ichimoku_indicators",
    "project_future_cloud",
    "detect_cloud_trampoline_bounce",
    "compute_institutional_flow_indicators",
    "build_ichimoku_series_payload",
    "QuantIndicators",
    "WeeklyTrendContext",
    "InstitutionalFlowContext",
    "TrampolineBounceContext",
    "QuantScoreBreakdown",
    "TierClassification",
    "calculate_canonical_bull_score",
    "calculate_canonical_sniper_score",
    "calculate_canonical_bear_score",
    "evaluate_quant_score",
    "classify_quant_tier",
    "classify_3tier_candidates",
    "evaluate_macro_stance",
    "calculate_msi_regime",
]
