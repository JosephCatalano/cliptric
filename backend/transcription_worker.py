"""Offline inference subprocess. Imports the ML runtime only inside this process."""

import argparse
import json
import math
import os
import sys
import traceback
import wave
from pathlib import Path
from uuid import UUID

from .files import write_json_atomic as write_json
from .transcription_models import MAX_RESULT_BYTES, MAX_WORDS, TranscriptWord

SAMPLE_RATE = 16_000
WINDOW_SECONDS = 300
CONTEXT_SECONDS = 2
TRANSCRIBE_OPTIONS = {
    "language": "en",
    "task": "transcribe",
    "beam_size": 5,
    "word_timestamps": True,
    "vad_filter": True,
    "vad_parameters": {"min_silence_duration_ms": 500},
    "condition_on_previous_text": False,
    "hallucination_silence_threshold": 2.0,
    "no_speech_threshold": 0.6,
    "compression_ratio_threshold": 2.4,
    "log_prob_threshold": -1.0,
    "log_progress": False,
}

_DLL_HANDLES = []


def configure_cuda_libraries():
    """Find optional NVIDIA runtime wheels inside this interpreter's environment."""
    if os.name != "nt" or _DLL_HANDLES:
        return
    for package in ("cublas", "cudnn", "cuda_nvrtc"):
        directory = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / package / "bin"
        if directory.is_dir():
            _DLL_HANDLES.append(os.add_dll_directory(str(directory)))
            # CTranslate2 loads some CUDA libraries through LoadLibrary by name.
            os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")


class InferenceRuntime:
    def __init__(self):
        self.key, self.model, self.profile = None, None, None

    def load(self, request):
        configure_cuda_libraries()
        key = (request["model_dir"], request["threads"], request.get("device", "cpu"))
        if key == self.key:
            return self.model, self.profile
        import numpy as np
        from faster_whisper import WhisperModel

        requested = request.get("device", "cpu")
        if requested not in {"auto", "cpu", "cuda"}:
            raise ValueError("Invalid transcription device")
        warning, device = None, "cpu"
        if requested != "cpu":
            import ctranslate2

            device = "cuda" if requested == "cuda" or ctranslate2.get_cuda_device_count() else "cpu"
            if device == "cpu":
                warning = "CUDA is unavailable. Used CPU transcription."
        compute = "int8_float16" if device == "cuda" else "int8"
        try:
            model = WhisperModel(
                request["model_dir"],
                device=device,
                compute_type=compute,
                cpu_threads=request["threads"],
                num_workers=1,
                local_files_only=True,
            )
            if device == "cuda":
                # Load the encoder/decoder libraries too, before any source words
                # are emitted. Device enumeration alone does not verify cuDNN.
                list(
                    model.transcribe(
                        np.zeros(SAMPLE_RATE, dtype=np.float32),
                        language="en",
                        beam_size=1,
                        vad_filter=False,
                    )[0]
                )
        except RuntimeError:
            if requested != "auto" or device != "cuda":
                raise
            device, compute = "cpu", "int8"
            warning = "CUDA could not initialize the speech model. Used CPU transcription; check CUDA/cuDNN setup."
            model = WhisperModel(
                request["model_dir"],
                device=device,
                compute_type=compute,
                cpu_threads=request["threads"],
                num_workers=1,
                local_files_only=True,
            )
        self.key, self.model = key, model
        self.profile = {"device": device, "compute_type": compute, "warning": warning}
        return model, self.profile


def transcribe(request: dict, runtime=None) -> None:
    # This process must not fetch models, tokenizers, or telemetry while handling media.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    import numpy as np

    work = Path(request["work_dir"])
    model, profile = (runtime or InferenceRuntime()).load(request)
    count = 0
    skipped = 0
    duplicate = 0
    previous: TranscriptWord | None = None
    with (
        wave.open(str(work / "audio.wav"), "rb") as audio,
        (work / "words.ndjson").open("x", encoding="utf-8", newline="\n") as result,
    ):
        if (
            audio.getnchannels() != 1
            or audio.getsampwidth() != 2
            or audio.getframerate() != SAMPLE_RATE
        ):
            raise ValueError("Decoded audio format did not pass verification.")
        frames = audio.getnframes()
        if not frames or frames / SAMPLE_RATE > request["duration"] + 0.1:
            raise ValueError("Decoded audio duration did not pass verification.")
        window = WINDOW_SECONDS * SAMPLE_RATE
        context = CONTEXT_SECONDS * SAMPLE_RATE
        for core_start in range(0, frames, window):
            boundary_previous = previous
            core_end = min(frames, core_start + window)
            read_start = max(0, core_start - context)
            read_end = min(frames, core_end + context)
            audio.setpos(read_start)
            samples = np.frombuffer(audio.readframes(read_end - read_start), dtype="<i2")
            if len(samples) != read_end - read_start:
                raise ValueError("Decoded audio is incomplete.")
            waveform = samples.astype(np.float32) / 32768.0
            segments, _ = model.transcribe(waveform, **TRANSCRIBE_OPTIONS)
            for segment in segments:
                for item in segment.words or []:
                    if not all(
                        math.isfinite(value) for value in (item.start, item.end, item.probability)
                    ):
                        raise ValueError("The model returned non-finite word timing.")
                    start = item.start + read_start / SAMPLE_RATE
                    end = item.end + read_start / SAMPLE_RATE
                    if start < 0 or end > request["duration"] + 0.05:
                        raise ValueError("The model returned word timing outside the recording.")
                    midpoint = (start + end) / 2
                    if not core_start / SAMPLE_RATE <= midpoint < core_end / SAMPLE_RATE:
                        continue
                    if end <= start or not item.word.strip():
                        skipped += 1
                        continue
                    word = TranscriptWord(
                        id=count,
                        start=start,
                        end=end,
                        text=item.word.strip(),
                        probability=item.probability,
                    )
                    # Overlapping context can produce the same boundary word twice.
                    if (
                        core_start > 0
                        and previous is boundary_previous
                        and previous
                        and word.start < previous.end
                        and word.text == previous.text
                    ):
                        duplicate += 1
                        continue
                    if previous and word.start < previous.start:
                        # Unusable model timing cannot safely drive captions or clipping.
                        # Omit it with the same persisted warning as other invalid timings.
                        skipped += 1
                        continue
                    if count >= MAX_WORDS:
                        raise ValueError("Transcription exceeded its word limit.")
                    result.write(word.model_dump_json() + "\n")
                    count += 1
                    previous = word
                    if result.tell() > MAX_RESULT_BYTES:
                        raise ValueError("Transcription exceeded its result size limit.")
                write_json(
                    work / "progress.json",
                    {
                        "seconds": min(
                            core_end / SAMPLE_RATE, segment.end + read_start / SAMPLE_RATE
                        )
                    },
                )
            write_json(work / "progress.json", {"seconds": core_end / SAMPLE_RATE})
        result.flush()
        os.fsync(result.fileno())
    write_json(
        work / "complete.json",
        {
            "word_count": count,
            "skipped_timing_words": skipped,
            "merged_boundary_words": duplicate,
            **({"runtime": profile} if "device" in request else {}),
        },
    )


def serve(root: Path):
    # The parent owns idle expiry. Read commands on the inference thread so
    # native ML libraries never initialize beside a blocking stdin reader.
    runtime = InferenceRuntime()
    while True:
        line = sys.stdin.buffer.readline(16_385)
        if not line or len(line) > 16_384:
            return 0 if not line else 1
        path = Path(json.loads(line)["request"])
        if (
            path.is_symlink()
            or path.resolve().parent.parent != root.resolve()
            or path.name != "request.json"
        ):
            raise ValueError("Invalid inference request location")
        UUID(path.parent.name)
        if path.stat().st_size > 16_384:
            raise ValueError("Invalid inference request size")
        request = json.loads(path.read_text(encoding="utf-8"))
        if Path(request["work_dir"]).resolve() != path.resolve().parent:
            raise ValueError("Inference work directory mismatch")
        try:
            transcribe(request, runtime)
        except Exception:
            (path.parent / "worker-error.log").write_text(
                traceback.format_exc()[-16_384:], encoding="utf-8"
            )
            write_json(
                path.parent / "worker_failed.json",
                {"error": "Speech inference failed. See the local transcription log."},
            )
            return 1  # Never reuse potentially damaged inference state.


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path, nargs="?")
    parser.add_argument("--serve", type=Path)
    args = parser.parse_args()
    try:
        if args.serve:
            return serve(args.serve)
        if args.request is None:
            raise ValueError("A request file is required")
        if args.request.stat().st_size > 16_384:
            raise ValueError("Invalid worker request size.")
        request = json.loads(args.request.read_text(encoding="utf-8"))
        transcribe(request)
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
