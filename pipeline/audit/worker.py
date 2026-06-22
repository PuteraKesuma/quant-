"""Audit worker: one cycle = integrity + repair + continuity + report per symbol.
`watch()` runs cycles forever on the configured interval until SIGINT."""
import signal
import time
from loguru import logger

from .integrity import check_integrity
from .continuity import classify_gaps
from .repair import repair_symbol
from .report import write_reports

_STOP = False


def _handle_stop(signum, frame):
    global _STOP
    _STOP = True
    logger.info("Stop requested — finishing current cycle, then exiting.")


def run_cycle(symbols, cfg):
    for sym in symbols:
        try:
            integ = check_integrity(sym, cfg)
            rep = repair_symbol(sym, cfg)            # no-op dict if repair disabled
            cont = classify_gaps(sym, cfg)           # final state for the report
            write_reports(sym, cfg, integ, cont, rep)

            issues = len(integ["findings"])
            anom = cont["anomaly_count"]
            extra = ""
            if rep.get("enabled"):
                extra = f" repaired(refetch={rep['refetched']},fill={rep['filled']})"
            logger.info(f"[{sym}] rows={integ['rows']:,} integrity_issues={issues} "
                        f"intraday_anomalies={anom}{extra}")
        except Exception as e:
            logger.error(f"[{sym}] audit failed (DB locked / fetch in progress?): {e}")


def watch(symbols, cfg):
    signal.signal(signal.SIGINT, _handle_stop)
    interval = int(cfg["audit"]["interval_minutes"] * 60)
    cycle = 0
    logger.info(f"Audit worker started — symbols={symbols} interval={interval}s "
                f"repair={cfg['audit'].get('enable_repair')}")
    while not _STOP:
        cycle += 1
        logger.info(f"===== Audit cycle {cycle} =====")
        run_cycle(symbols, cfg)
        if _STOP:
            break
        logger.info(f"Cycle {cycle} done. Sleeping {interval}s.")
        slept = 0
        while slept < interval and not _STOP:
            time.sleep(min(2, interval - slept))
            slept += 2
    logger.info("Audit worker stopped.")
