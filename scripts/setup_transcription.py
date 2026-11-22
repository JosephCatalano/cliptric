"""Install the pinned local English transcription model with integrity checks."""

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx2

from backend.config import TRANSCRIPTION_REPO, TRANSCRIPTION_REVISION, Settings
from backend.files import require_space
from backend.model_assets import MODEL_FILES

MIRROR_REVISION = "703e5c6d150ca27903b1e6ff8955b345633f4c6f"


def verify_file(path: Path, size: int, checksum: str) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
        raise ValueError(f"Model file is missing or has an unexpected size: {path.name}")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != checksum:
        raise ValueError(f"Model integrity check failed: {path.name}")


def install(destination: Path, source: str, local: Path | None = None) -> None:
    destination = destination.resolve()
    if destination.exists():
        for name, (size, checksum) in MODEL_FILES.items():
            verify_file(destination / name, size, checksum)
        print(f"Verified model files already installed at {destination}")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        require_space(
            destination.parent, 256 * 1024**2, sum(size for size, _ in MODEL_FILES.values())
        )
        with tempfile.TemporaryDirectory(prefix=".model-", dir=destination.parent) as temporary:
            staging = Path(temporary) / "small.en"
            staging.mkdir()
            with httpx2.Client(
                follow_redirects=True, timeout=httpx2.Timeout(60, connect=20)
            ) as client:
                for name, (size, checksum) in MODEL_FILES.items():
                    target = staging / name
                    if local is not None:
                        verify_file(local / name, size, checksum)
                        shutil.copyfile(local / name, target)
                    else:
                        url = (
                            f"https://huggingface.co/{TRANSCRIPTION_REPO}/resolve/{TRANSCRIPTION_REVISION}/{name}"
                            if source == "huggingface"
                            else "https://modelscope.cn/api/v1/models/gpustack/faster-whisper-small.en/repo"
                            f"?Revision={MIRROR_REVISION}&FilePath={name}"
                        )
                        print(f"Downloading {name} ({size / 1024**2:.1f} MiB)...", flush=True)
                        received = 0
                        with client.stream("GET", url) as response, target.open("xb") as output:
                            response.raise_for_status()
                            for chunk in response.iter_bytes(1024**2):
                                received += len(chunk)
                                if received > size:
                                    raise ValueError(
                                        f"Model download exceeds expected size: {name}"
                                    )
                                output.write(chunk)
                    verify_file(target, size, checksum)
                    print(f"Verified {name}", flush=True)
            staging.rename(destination)
    manifest = {
        "repo_id": TRANSCRIPTION_REPO,
        "revision": TRANSCRIPTION_REVISION,
        "files": {name: checksum for name, (_, checksum) in MODEL_FILES.items()},
    }
    temporary_manifest = destination / "clipping-model.json.tmp"
    temporary_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary_manifest.replace(destination / "clipping-model.json")
    print("Local English transcription model is ready.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("huggingface", "gpustack"), default="huggingface")
    parser.add_argument(
        "--from-directory",
        type=Path,
        help="Install verified model data copied from another machine.",
    )
    args = parser.parse_args()
    try:
        install(Settings.load().transcription_model_dir, args.source, args.from_directory)
    except (OSError, ValueError, httpx2.HTTPError) as error:
        parser.exit(1, f"Model setup failed: {error}\nExisting model files were preserved.\n")


if __name__ == "__main__":
    main()
