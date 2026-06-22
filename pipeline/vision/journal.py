"""Write-only audit sink for the vision strategy.

Each decision appends one JSON line to the journal file; the screenshot the model
saw is archived to disk for visual audit. Pure sink — it knows nothing about
trading logic. Paths come from `config.yaml` (`vision.journal_path`,
`vision.archive_dir`); screenshots are archived only when the action changed
(disk-friendly) unless `archive_all_frames` is set.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger


class VisionJournal:
    """Append-only JSONL journal + PNG archive."""

    def __init__(self, cfg: dict):
        vcfg = cfg.get("vision", {}) or {}
        self.journal_path = Path(vcfg.get("journal_path", "vision_journal.jsonl"))
        self.archive_dir = Path(vcfg.get("archive_dir", "_DOC/vision"))
        self._global_archive_all = bool(vcfg.get("archive_all_frames", False))

    def record(self, symbol: str, name: str, png: bytes, decision: dict,
               signal_id: str, action_changed: bool, archive_all: bool = False) -> None:
        """Append one journal line; archive the PNG when the action changed
        (or when `archive_all` / the global `archive_all_frames` is set)."""
        ts = datetime.now(timezone.utc).isoformat()
        row = {
            "ts": ts,
            "symbol": symbol,
            "name": name,
            "action": decision.get("action"),
            "confidence": decision.get("confidence"),
            "sl": decision.get("sl"),
            "tp": decision.get("tp"),
            "reason": decision.get("reason"),
            "structure": decision.get("structure"),
            "key_levels": decision.get("key_levels"),
            "signal_id": signal_id,
            "action_changed": action_changed,
        }
        try:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.journal_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as e:                         # journal must never break trading
            logger.warning(f"[vision:{symbol}] journal write failed: {e}")

        if png and (action_changed or archive_all or self._global_archive_all):
            try:
                self.archive_dir.mkdir(parents=True, exist_ok=True)
                safe_ts = ts.replace(":", "-")          # Windows-safe filename
                (self.archive_dir / f"{safe_ts}_{symbol}.png").write_bytes(png)
            except Exception as e:
                logger.warning(f"[vision:{symbol}] screenshot archive failed: {e}")
