"""Canonical research utilities for the Freqtrade Strategy Lab."""

from .dataset import MarketFile, discover_market_files, load_ohlcv
from .event_study import EventStudyConfig, analyze_pair, build_candidate_tables
from .experiment import ExperimentSpec, tag_result_frame

__all__ = [
    "EventStudyConfig",
    "ExperimentSpec",
    "MarketFile",
    "analyze_pair",
    "build_candidate_tables",
    "discover_market_files",
    "load_ohlcv",
    "tag_result_frame",
]

