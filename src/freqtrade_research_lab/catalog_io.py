from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


CATALOG_NAME = "dataset_catalog.csv"
SUMMARY_NAME = "dataset_catalog_summary.json"


def write_catalog_bundle(
    catalog: pd.DataFrame,
    summary: dict[str, object],
    output_dir: Path,
    *,
    replace: bool = False,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = output_dir / CATALOG_NAME
    summary_path = output_dir / SUMMARY_NAME

    existing = [path for path in (catalog_path, summary_path) if path.exists()]
    if existing and not replace:
        names = ", ".join(path.name for path in existing)
        raise FileExistsError(
            f"Catalog output already exists ({names}). "
            "Use a different --output-dir or pass --replace explicitly."
        )

    catalog_tmp = output_dir / f".{CATALOG_NAME}.tmp"
    summary_tmp = output_dir / f".{SUMMARY_NAME}.tmp"
    try:
        catalog.to_csv(catalog_tmp, index=False)
        summary_tmp.write_text(
            json.dumps(summary, indent=2),
            encoding="utf-8",
        )
        catalog_tmp.replace(catalog_path)
        summary_tmp.replace(summary_path)
    finally:
        catalog_tmp.unlink(missing_ok=True)
        summary_tmp.unlink(missing_ok=True)
