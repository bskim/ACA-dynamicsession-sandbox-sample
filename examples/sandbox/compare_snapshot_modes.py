"""Compare Memory and Disk lifecycle modes; the default run is simulated."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aca_agent_sample.auth import create_credential
from aca_agent_sample.config import load_config
from aca_agent_sample.sandboxes.client import AcaSandboxAdapter, FakeSandboxAdapter
from aca_agent_sample.sandboxes.lifecycle_benchmark import (
    SandboxLifecycleComparator, metrics_to_csv, metrics_to_json, metrics_to_markdown,
)

WARNING = "Memory mode does not support Data Disk volumes. Simulated timings are not Azure measurements."


def render_markdown(samples):
    lines = ["# Memory vs. Disk lifecycle comparison", "", WARNING, "",
             "Disk witness: `/workspace/demo/sentinel.txt` (SHA-256).", "",
             "| Mode | Iteration | Disk SHA-256 preserved | worker_boot_nonce preserved | in_memory_counter continuous | buffer_checksum preserved | Memory preserved | Worker restarted | Oracle passed |",
             "| --- | ---: | --- | --- | --- | --- | --- | --- | --- |"]
    for sample in samples:
        oracle, before, after = sample.oracle, sample.before, sample.after
        if oracle and before and after:
            nonce = bool(after.worker_alive and before.worker_boot_nonce == after.worker_boot_nonce)
            counter = bool(after.worker_alive and type(after.in_memory_counter) is int and after.in_memory_counter > before.in_memory_counter)
            checksum = bool(after.worker_alive and before.buffer_checksum == after.buffer_checksum)
            values = [oracle.disk_preserved, nonce, counter, checksum, oracle.memory_preserved, oracle.worker_restarted, oracle.passed]
        else:
            values = ["n/a"] * 7
        lines.append(f"| {sample.mode} | {sample.iteration} | " + " | ".join(map(str, values)) + " |")
    return "\n".join(lines) + "\n\n" + metrics_to_markdown(samples)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use Azure only when ENABLE_LIVE_AZURE=true")
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, help="Write JSON, CSV and Markdown reports to this directory")
    parser.add_argument("--format", choices=("text", "json", "markdown", "csv"), default="text")
    args = parser.parse_args(argv)
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    credential = None
    try:
        if args.live:
            config = load_config()
            if not config.enable_live_azure:
                parser.error("--live requires ENABLE_LIVE_AZURE=true")
            config.validate("sandbox")
            credential = create_credential(config)
            manifest = ROOT / ".internal" / "runs" / f"compare-{uuid.uuid4().hex}.json"
            adapter = AcaSandboxAdapter(config, credential, manifest_path=str(manifest))
        else:
            adapter = FakeSandboxAdapter()
        samples = SandboxLifecycleComparator(adapter).run(iterations=args.iterations)
        data = json.loads(metrics_to_json(samples))
        data.update({"simulation": not args.live, "warning": WARNING,
                     "disk_witness": "/workspace/demo/sentinel.txt"})
        reports = {"json": json.dumps(data, indent=2, allow_nan=False) + "\n",
                   "csv": metrics_to_csv(samples), "markdown": render_markdown(samples)}
        if args.output_dir is not None:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            for ext, key in (("json", "json"), ("csv", "csv"), ("md", "markdown")):
                (args.output_dir / f"snapshot-comparison.{ext}").write_text(reports[key], encoding="utf-8")
        print(reports["markdown" if args.format == "text" else args.format], end="")
        return 0 if all(sample.success for sample in samples) else 1
    except Exception:
        print("Comparison failed; details suppressed to protect sensitive configuration.", file=sys.stderr)
        return 1
    finally:
        if credential is not None:
            credential.close()


if __name__ == "__main__":
    raise SystemExit(main())
