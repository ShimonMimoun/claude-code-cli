"""Mirror Claude Code installers from GCS to a local directory.

Downloads platform-specific binaries, verifies their SHA-256 checksums,
and organises them into the folder structure served by ``install_server.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import httpx

from claude_code_internal.logging_config import get_logger

logger = get_logger(__name__)

GCS_BUCKET = (
    "https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819"
    "/claude-code-releases"
)


@dataclass(frozen=True)
class PlatformInfo:
    """Metadata for a single platform binary in the release manifest."""

    platform: str
    filename: str
    checksum: str


# ── Helpers ─────────────────────────────────────────────────────────────────


def _sha256_file(path: Path) -> str:
    """Return the hex-encoded SHA-256 digest of *path*."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, timeout_s: float = 120.0) -> None:
    """Stream-download *url* to *dest*, creating parent dirs as needed."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading %s → %s", url, dest)
    with httpx.stream("GET", url, follow_redirects=True, timeout=timeout_s) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)


# ── Manifest parsing ───────────────────────────────────────────────────────


def get_latest_version() -> str:
    """Fetch the latest release version string from GCS."""
    r = httpx.get(f"{GCS_BUCKET}/latest", timeout=10.0)
    r.raise_for_status()
    version = r.text.strip()
    logger.info("Latest version: %s", version)
    return version


def get_manifest(version: str) -> dict:
    """Fetch the release manifest for *version*."""
    r = httpx.get(f"{GCS_BUCKET}/{version}/manifest.json", timeout=10.0)
    r.raise_for_status()
    return r.json()


def iter_platforms(manifest: dict) -> Iterable[PlatformInfo]:
    """Yield :class:`PlatformInfo` entries from a release manifest."""
    platforms = manifest.get("platforms", {})
    for platform, info in platforms.items():
        yield PlatformInfo(
            platform=platform, filename=info["binary"], checksum=info["checksum"]
        )


# ── Path mapping ───────────────────────────────────────────────────────────

_PLATFORM_MAP = {
    "win32": ("windows", "claude-code-setup.exe"),
    "darwin": ("macos", "claude-code-setup.pkg"),
    "linux": ("linux", "claude-code-setup.run"),
}


def _platform_to_internal_path(platform: str) -> Tuple[str, str, str]:
    """Map a GCS platform key to ``(os_dir, subdir, filename)``."""
    for prefix, (os_dir, filename) in _PLATFORM_MAP.items():
        if platform.startswith(f"{prefix}-"):
            return os_dir, platform, filename
    raise ValueError(f"Unsupported platform: {platform}")


# ── Mirror logic ───────────────────────────────────────────────────────────


def mirror(
    output_dir: Path,
    version: Optional[str] = None,
    platforms_allowlist: Optional[Iterable[str]] = None,
) -> Dict[str, Path]:
    """Download and verify Claude Code binaries into *output_dir*.

    Parameters
    ----------
    output_dir:
        Destination folder (typically ``install_artifacts/``).
    version:
        Specific version to fetch; defaults to latest.
    platforms_allowlist:
        If given, only download matching platform keys.

    Returns
    -------
    dict
        Mapping of platform key → destination :class:`Path`.
    """
    if version is None:
        version = get_latest_version()
    manifest = get_manifest(version)

    allow = set(platforms_allowlist) if platforms_allowlist else None
    downloaded: Dict[str, Path] = {}

    for p in iter_platforms(manifest):
        if allow is not None and p.platform not in allow:
            continue

        os_dir, subdir, internal_name = _platform_to_internal_path(p.platform)
        url = f"{GCS_BUCKET}/{version}/{p.platform}/{p.filename}"
        dest = output_dir / os_dir / subdir / internal_name
        _download(url, dest)

        actual = _sha256_file(dest)
        if actual.lower() != p.checksum.lower():
            dest.unlink(missing_ok=True)
            raise RuntimeError(
                f"Checksum mismatch for {p.platform}: expected {p.checksum}, got {actual}"
            )
        logger.info("✓ %s checksum verified", p.platform)
        downloaded[p.platform] = dest

    # Copy preferred platform as the default fallback binary
    def _copy_default(
        os_name: str, preferred_platforms: Iterable[str], filename: str
    ) -> Optional[Path]:
        for plat in preferred_platforms:
            src = downloaded.get(plat)
            if src:
                dst = output_dir / os_name / filename
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                if os_name != "windows":
                    dst.chmod(0o755)
                logger.info("Default %s binary: %s → %s", os_name, plat, dst)
                return dst
        return None

    _copy_default("windows", ["win32-x64", "win32-arm64"], "claude-code-setup.exe")
    _copy_default("macos", ["darwin-arm64", "darwin-x64"], "claude-code-setup.pkg")
    _copy_default(
        "linux",
        ["linux-x64", "linux-x64-musl", "linux-arm64", "linux-arm64-musl"],
        "claude-code-setup.run",
    )

    (output_dir / "VERSION").write_text(version + "\n", encoding="utf-8")
    logger.info("Mirror complete — %d platform(s), version %s", len(downloaded), version)
    return downloaded


# ── CLI entry point ─────────────────────────────────────────────────────────


def main(argv: Optional[list] = None) -> int:
    """CLI entry point: ``mirror-claude-code [--output DIR] [--version V] [--platform P]``."""
    parser = argparse.ArgumentParser(
        description="Mirror Claude Code installers to internal server folder"
    )
    parser.add_argument(
        "--output", default="install_artifacts", help="Output folder (served by install server)"
    )
    parser.add_argument("--version", default=None, help="Version to mirror (default: latest)")
    parser.add_argument(
        "--platform",
        action="append",
        dest="platforms",
        default=None,
        help="Repeatable platform allowlist entry (e.g. --platform win32-x64)",
    )
    args = parser.parse_args(argv)

    out = Path(args.output)
    downloaded = mirror(out, version=args.version, platforms_allowlist=args.platforms)
    logger.info("Mirrored %d artifact(s) into: %s", len(downloaded), out.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
