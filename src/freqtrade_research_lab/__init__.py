"""Canonical research utilities for the Freqtrade Strategy Lab."""

from .catalog_io import write_catalog_bundle
from .comparison import apply_global_fdr, build_system_coverage, combine_discovery_frames
from .dataset import (
    CatalogSelection,
    MarketFile,
    discover_market_files,
    fingerprint_market_files,
    load_catalog_selection,
    load_ohlcv,
    sha256_file,
)
from .event_study import (
    EventStudyConfig,
    analyze_pair,
    analyze_signals,
    build_candidate_tables,
    build_discovery_table,
)
from .experiment import ExperimentSpec, tag_result_frame
from .run_io import prepare_fresh_output_dir
from .signals import SignalSet, macd_crossover_signals, validate_signal_set

__all__ = [
    "CatalogSelection",
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
    "fingerprint_market_files",
    "load_catalog_selection",
    "load_ohlcv",
    "macd_crossover_signals",
    "prepare_fresh_output_dir",
    "sha256_file",
    "tag_result_frame",
    "validate_signal_set",
    "write_catalog_bundle",
]

