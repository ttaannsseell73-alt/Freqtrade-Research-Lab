"""Canonical research utilities for the Freqtrade Strategy Lab."""

from .comparison import build_system_coverage, combine_discovery_frames
from .dataset import MarketFile, discover_market_files, load_ohlcv
from .event_study import (
    EventStudyConfig,
    analyze_pair,
    build_candidate_tables,
    build_discovery_table,
)
from .experiment import ExperimentSpec, tag_result_frame

__all__ = [
    "EventStudyConfig",
    "ExperimentSpec",
    "MarketFile",
    "analyze_pair",
    "build_candidate_tables",
    "build_discovery_table",
    "build_system_coverage",
    "combine_discovery_frames",
    "discover_market_files",
    "load_ohlcv",
    "tag_result_frame",
]

