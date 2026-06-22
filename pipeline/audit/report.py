"""Write per-symbol audit reports: human-readable markdown + machine JSON."""
import json
from . import ROOT


def _md(symbol, integ, cont, rep) -> str:
    lines = [f"# {symbol} — Data Audit", ""]
    lines += [
        f"- Rows: **{integ['rows']:,}**",
        f"- Range (UTC): {integ['range'][0]} → {integ['range'][1]}",
        f"- Daily break (auto-detected): {cont['daily_break']}",
        f"- Gaps: {cont['n_gaps']} total | intraday anomalies: {cont['anomaly_count']} "
        f"| missing bars: {cont['missing_bars_anomaly']:,}",
    ]
    if rep and rep.get("enabled"):
        lines.append(
            f"- Repair: refetched={rep.get('refetched', 0):,}, "
            f"filled(synthetic)={rep.get('filled', 0):,}, "
            f"anomalies {rep.get('anomalies_before', 0)}→{rep.get('anomalies_after', 0)}"
        )
    lines += ["", "## Integrity", "", "| check | severity | count |", "|---|---|---|"]
    if integ["findings"]:
        for f in integ["findings"]:
            lines.append(f"| {f['check']} | {f['severity']} | {f['count']:,} |")
    else:
        lines.append("| (none) | ok | 0 |")

    lines += ["", "## Gap classification", "", "| class | count |", "|---|---|"]
    for k, v in sorted(cont["by_class"].items()):
        lines.append(f"| {k} | {v:,} |")

    lines += ["", "## Completeness by year", "",
              "| year | bars | missing | completeness % |", "|---|---|---|---|"]
    for y, s in sorted(cont["by_year"].items()):
        lines.append(f"| {y} | {s['bars']:,} | {s['missing']:,} | {s['completeness']} |")

    return "\n".join(lines) + "\n"


def write_reports(symbol, cfg, integ, cont, rep):
    rdir = ROOT / cfg["audit"]["report_dir"]
    gdir = ROOT / cfg["audit"]["gaps_dir"]
    rdir.mkdir(parents=True, exist_ok=True)
    gdir.mkdir(parents=True, exist_ok=True)

    (rdir / f"{symbol}_audit.md").write_text(_md(symbol, integ, cont, rep), encoding="utf-8")

    summary = {
        "symbol": symbol,
        "integrity": integ,
        "continuity": {k: v for k, v in cont.items() if k != "gaps"},
        "repair": rep,
    }
    (gdir / f"{symbol}_audit.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (gdir / f"{symbol}_gaps.json").write_text(
        json.dumps(cont["gaps"], indent=2, default=str), encoding="utf-8")
