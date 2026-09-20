"""Canonical research utilities for the Freqtrade Strategy Lab."""

from .dataset import MarketFile, discover_market_files, load_ohlcv
from .event_study import EventStudyConfig, analyze_pair, build_candidate_tables

__all__ = [
    "EventStudyConfig",
    "MarketFile",
    "analyze_pair",
    "build_candidate_tables",
    "discover_market_files",
    "load_ohlcv",
]

