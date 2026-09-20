from __future__ import annotations

from pathlib import Path


def prepare_fresh_output_dir(output_dir: Path) -> None:
    if output_dir.exists():
        if not output_dir.is_dir():
            raise FileExistsError(f"Output path exists and is not a directory: {output_dir}")
        existing = sorted(path.name for path in output_dir.iterdir())
        if existing:
            preview = ", ".join(existing[:5])
            suffix = "" if len(existing) <= 5 else f" (+{len(existing) - 5} more)"
            raise FileExistsError(
                f"Output directory is not empty: {output_dir} [{preview}{suffix}]. "
                "Use a fresh run directory."
            )
    else:
        output_dir.mkdir(parents=True, exist_ok=False)
