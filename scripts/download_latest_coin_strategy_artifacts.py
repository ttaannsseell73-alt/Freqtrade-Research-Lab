from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import urllib.request
import urllib.parse
import zipfile


API = "https://api.github.com"


def _request_json(url: str, token: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "CoinStrategyLab-Router/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request_bytes(url: str, token: str) -> bytes:
    """Download a GitHub artifact without leaking auth to the signed storage host."""
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "CoinStrategyLab-Router/1.0",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code not in {301, 302, 303, 307, 308}:
            raise
        location = exc.headers.get("Location")
        if not location:
            raise RuntimeError("Artifact download redirect did not include Location") from exc

    signed_request = urllib.request.Request(
        location,
        headers={"User-Agent": "CoinStrategyLab-Router/1.0"},
    )
    with urllib.request.urlopen(signed_request, timeout=180) as response:
        return response.read()


def latest_artifact(repo: str, name: str, token: str, branch: str | None = None) -> dict | None:
    url = f"{API}/repos/{repo}/actions/artifacts?name={urllib.parse.quote(name)}&per_page=100"
    payload = _request_json(url, token)
    candidates = [
        item for item in payload.get("artifacts", [])
        if item.get("name") == name
        and not item.get("expired", False)
        and (
            not branch
            or (item.get("workflow_run") or {}).get("head_branch") == branch
        )
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x.get("created_at", ""), int(x.get("id", 0))), reverse=True)
    return candidates[0]


def download_artifact(repo: str, name: str, token: str, destination: Path, branch: str | None = None) -> dict:
    artifact = latest_artifact(repo, name, token, branch)
    if artifact is None:
        raise FileNotFoundError(f"Required GitHub Actions artifact not found: {name}")
    payload = _request_bytes(
        f"{API}/repos/{repo}/actions/artifacts/{artifact['id']}/zip",
        token,
    )
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(destination)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", action="append", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", ""))
    parser.add_argument("--branch", default=os.getenv("GITHUB_REF_NAME", ""))
    args = parser.parse_args()

    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")
    if not args.repo:
        raise RuntimeError("Repository must be supplied via --repo or GITHUB_REPOSITORY")

    manifest = []
    missing = []
    for name in args.artifact:
        try:
            artifact = download_artifact(
                args.repo,
                name,
                token,
                args.output_root / name,
                args.branch or None,
            )
            manifest.append(
                {
                    "name": name,
                    "artifact_id": artifact["id"],
                    "created_at": artifact.get("created_at"),
                    "workflow_run_id": (artifact.get("workflow_run") or {}).get("id"),
                    "head_branch": (artifact.get("workflow_run") or {}).get("head_branch"),
                }
            )
            print(f"downloaded {name} artifact_id={artifact['id']}", flush=True)
        except FileNotFoundError:
            missing.append(name)

    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "ARTIFACT_MANIFEST.json").write_text(
        json.dumps({"artifacts": manifest, "missing": missing}, indent=2),
        encoding="utf-8",
    )
    if missing:
        raise SystemExit("Missing required artifacts: " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
