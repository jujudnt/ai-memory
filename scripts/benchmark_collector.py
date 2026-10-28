"""Compare collection CPU time and sampled RSS with a Git revision, using synthetic data."""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path


def worker():
    import psutil
    from aimemory.service import MemoryService

    with tempfile.TemporaryDirectory(prefix="ai-memory-benchmark-") as directory:
        root = Path(directory)
        os.environ["AI_MEMORY_HOME"] = str(root / "memory")
        home = root / "codex"
        source = home / "sessions/benchmark.jsonl"
        source.parent.mkdir(parents=True)
        # Repeated telemetry plus real messages/tools models long active JSONL logs.
        with source.open("w", encoding="utf-8") as output:
            output.write(json.dumps({"type": "session_meta", "payload": {"id": "benchmark"}}) + "\n")
            for index in range(1200):
                payload = ({"type": "message", "role": "user", "content": f"Message {index} " + "a" * 16384}
                           if index % 10 == 0 else {"type": "token_count", "info": "x" * 16384})
                output.write(json.dumps({"type": "event_msg", "timestamp": "2026-10-01T10:00:00Z", "payload": payload}) + "\n")
        source_bytes = source.stat().st_size
        process = psutil.Process()
        stopped = threading.Event()
        rss = [process.memory_info().rss]
        def sample():
            while not stopped.wait(.02):
                rss.append(process.memory_info().rss)
        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        service = MemoryService()
        wall, cpu = time.perf_counter(), time.process_time()
        first = service.import_codex(home)
        assert not first.errors and first.imported == 1, first
        initial = {"wall_seconds": time.perf_counter() - wall, "cpu_seconds": time.process_time() - cpu}
        samples = []
        for index in range(5):
            with source.open("a", encoding="utf-8") as output:
                output.write(json.dumps({"type": "response_item", "timestamp": f"2026-10-01T10:01:0{index}Z",
                                         "payload": {"type": "message", "role": "assistant", "content": f"Append {index}"}}) + "\n")
            wall, cpu = time.perf_counter(), time.process_time()
            result = service.import_codex(home)
            assert not result.errors and result.imported == 1, result
            samples.append({"wall_seconds": time.perf_counter() - wall, "cpu_seconds": time.process_time() - cpu})
        stopped.set()
        sampler.join()
        print(json.dumps({"source_bytes": source_bytes, "initial": initial, "appends": samples,
                          "sampled_peak_rss_bytes": max(rss), "conversations": service.db.status()["conversation_count"]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--baseline-ref", default="v0.4.37")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker()
        return
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="ai-memory-baseline-") as directory:
        baseline = Path(directory)
        archive = baseline / "source.zip"
        subprocess.run(["git", "archive", "--format=zip", "--output", str(archive), args.baseline_ref], cwd=repo, check=True)
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(baseline / "source")
        report = {"platform": platform.platform(), "python": platform.python_version(), "baseline_ref": args.baseline_ref,
                  "note": "Synthetic local import only. Sampled RSS is not a hard memory cap. No real cloud or battery measurement."}
        for label, source in (("baseline", baseline / "source/src"), ("optimized", repo / "src")):
            completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker"],
                                       env={**os.environ, "PYTHONPATH": str(source)}, cwd=baseline,
                                       capture_output=True, text=True, timeout=180)
            if completed.returncode:
                raise RuntimeError(completed.stderr or completed.stdout)
            report[label] = json.loads(completed.stdout)
        for label in ("baseline", "optimized"):
            samples = report[label]["appends"]
            report[label]["mean_append_cpu_seconds"] = sum(sample["cpu_seconds"] for sample in samples) / len(samples)
        report["append_cpu_reduction_percent"] = 100 * (1 - report["optimized"]["mean_append_cpu_seconds"] / report["baseline"]["mean_append_cpu_seconds"])
        rendered = json.dumps(report, indent=2)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)


if __name__ == "__main__":
    main()
