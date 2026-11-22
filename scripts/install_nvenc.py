"""Install a checksum-pinned, driver-compatible Windows encoder locally.

Run from the repository root. Does not change PATH or the system driver.
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
URL = (
    "https://github.com/GyanD/codexffmpeg/releases/download/8.0.1/ffmpeg-8.0.1-essentials_build.zip"
)
SHA256 = "e2aaeaa0fdbc397d4794828086424d4aaa2102cef1fb6874f6ffd29c0b88b673"


def main():
    if os.name != "nt":
        raise RuntimeError(
            "This optional installer is for Windows. Configure CLIPPING_NVENC_FFMPEG on other systems."
        )
    with tempfile.TemporaryDirectory(prefix="cliptric-nvenc-") as temporary:
        folder = Path(temporary)
        archive = folder / "ffmpeg.zip"
        with urllib.request.urlopen(URL, timeout=60) as response, archive.open("xb") as output:
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 256 * 1024**2:
                    raise RuntimeError("Encoder archive exceeded its size limit.")
                output.write(chunk)
        with archive.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != SHA256:
                raise RuntimeError("Encoder checksum mismatch. Nothing was installed.")
        extracted = folder / "encoder"
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                relative = PurePosixPath(*PurePosixPath(member.filename).parts[1:])
                if str(relative) not in {"bin/ffmpeg.exe", "LICENSE", "README.txt"}:
                    continue
                destination = extracted.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output)
        binary = extracted / "bin" / "ffmpeg.exe"
        subprocess.run(
            [
                str(binary),
                "-v",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "color=s=256x256:r=1:d=1",
                "-frames:v",
                "1",
                "-an",
                "-c:v",
                "h264_nvenc",
                "-f",
                "null",
                "-",
            ],
            check=True,
            timeout=30,
        )
        target = ROOT / ".tools" / "nvenc" / "ffmpeg"
        for relative in ("bin/ffmpeg.exe", "LICENSE", "README.txt"):
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            staging = destination.with_suffix(destination.suffix + ".installing")
            try:
                shutil.copyfile(extracted / relative, staging)
                staging.replace(destination)
            finally:
                staging.unlink(missing_ok=True)
    print("Installed and verified local NVIDIA encoder. Restart the backend to use it.")


if __name__ == "__main__":
    main()
