"""Canonical research utilities for the Freqtrade Strategy Lab."""

from .catalog_io import write_catalog_bundle
from .comparison import apply_global_fdr, build_system_coverage, combine_discovery_frames
from .dataset import MarketFile, discover_market_files, load_ohlcv
from .event_study import (
    EventStudyConfig,
    analyze_pair,
    analyze_signals,
    build_candidate_tables,
    build_discovery_table,
)
from .experiment import ExperimentSpec, tag_result_frame
from .signals import SignalSet, macd_crossover_signals, validate_signal_set

__all__ = [
    "EventStudyConfig",
    "ExperimentSpec",
    "MarketFile",
    "SignalSet",
    "analyze_pair",
    "analyze_signals",
    "apply_global_fdr",
    "build_candidate_tables",
    "build_discovery_table",
    "build_system_coverage",
    "combine_discovery_frames",
    "discover_market_files",
    "load_ohlcv",
    "macd_crossover_signals",
    "tag_result_frame",
    "validate_signal_set",
    "write_catalog_bundle",
]

