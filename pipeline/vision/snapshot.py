"""Capture a fresh chart for every vision slot and save it under archive_dir.

For quick manual verification ("is the vision capture correct?"). Saves each
slot's chart to `<archive_dir>/_CEK_<SYMBOL>.png` (fixed name = easy to open from
a .bat). Prints the absolute paths. Does NOT call the Claude API - capture only.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    slots = [s for s in cfg.get("live", {}).get("strategies", [])
             if s.get("type") == "vision"]
    if not slots:
        print("Tidak ada slot vision di config.yaml - tidak ada yang di-capture.")
        return 0

    from pipeline.vision.capture import ChartCapturer
    archive = ROOT / cfg.get("vision", {}).get("archive_dir", "_DOC/vision")
    archive.mkdir(parents=True, exist_ok=True)

    saved: list[Path] = []
    for spec in slots:
        sym = spec["symbol"]
        try:
            png = ChartCapturer(spec, cfg).capture(sym)
            out = archive / f"_CEK_{sym}.png"
            out.write_bytes(png)
            saved.append(out)
            print(f"[ OK ] {spec['name']:14} {sym} -> {out}  ({len(png)//1024} KB)")
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {spec['name']:14} {sym}: {e}")

    if saved:
        print("\nMembuka gambar terbaru...")
        import os
        os.startfile(saved[0])  # type: ignore[attr-defined]  # Windows only
    return 0


if __name__ == "__main__":
    sys.exit(main())
