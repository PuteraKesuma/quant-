"""Import a Databento GLBX ohlcv-1m DBN file into Level_0_Raw DuckDB.

A `.FUT` parent DBN contains every contract (outright + calendar spreads).
This builds a continuous **front-month** series: for each UTC day, pick the
outright contract with the highest volume and keep its 1m bars. Output matches
the standard Level_0 `ohlcv` schema plus a `contract` column for roll provenance.

  python -m pipeline.fetch.import_databento \
      --dbn data/databento/glbx-mdp3-20210609-20260608.ohlcv-1m.dbn --symbol MGC
"""
import argparse
import re
from pathlib import Path
import duckdb
import pandas as pd
import yaml
import databento as db
from loguru import logger

ROOT = Path(__file__).parent.parent.parent
_MONTH_CODES = "FGHJKMNQUVXZ"


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def extract_front_month(dbn_path: Path, root: str) -> pd.DataFrame:
    logger.info(f"Reading {dbn_path.name} ...")
    store = db.DBNStore.from_file(str(dbn_path))
    df = store.to_df(map_symbols=True)          # float prices, UTC index, `symbol`
    logger.info(f"  {len(df):,} total rows ({df.index.min()} → {df.index.max()})")

    # keep only outright contracts (e.g. MGCM5); drop spreads (contain '-')
    pat = re.compile(rf"^{re.escape(root)}[{_MONTH_CODES}]\d+$")
    out = df[df["symbol"].astype(str).str.match(pat)].copy()
    logger.info(f"  {len(out):,} outright rows across {out['symbol'].nunique()} contracts")

    # front month per day = contract with the most volume that UTC day
    out["day"] = out.index.normalize()
    vol = out.groupby(["day", "symbol"])["volume"].sum()
    front = vol.groupby(level=0).idxmax().map(lambda x: x[1])   # day -> symbol
    out["front"] = out["day"].map(front)
    sel = out[out["symbol"] == out["front"]].sort_index()

    result = pd.DataFrame({
        "ts":       sel.index,
        "open":     sel["open"].astype("float64").values,
        "high":     sel["high"].astype("float64").values,
        "low":      sel["low"].astype("float64").values,
        "close":    sel["close"].astype("float64").values,
        "volume":   sel["volume"].astype("float64").values,
        "contract": sel["symbol"].astype(str).values,
    })
    result = result.drop_duplicates(subset="ts").reset_index(drop=True)
    n_rolls = (result["contract"] != result["contract"].shift()).sum() - 1
    logger.info(f"  continuous front-month: {len(result):,} rows, "
                f"{result['contract'].nunique()} contracts, ~{n_rolls} rolls")
    return result


def write_duckdb(df: pd.DataFrame, symbol: str, cfg: dict):
    db_path = ROOT / cfg["data"]["raw_dir"] / f"{symbol}_1m.duckdb"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            ts        TIMESTAMPTZ PRIMARY KEY,
            open      DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume    DOUBLE,
            contract  VARCHAR
        )
    """)
    con.execute("INSERT OR REPLACE INTO ohlcv (ts,open,high,low,close,volume,contract) "
                "SELECT ts,open,high,low,close,volume,contract FROM df")
    n = con.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
    con.close()
    logger.info(f"[{symbol}] wrote {len(df):,} rows → {db_path}  (table now {n:,} rows)")


def main():
    cfg = load_config()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dbn", required=True, help="path to .dbn file")
    parser.add_argument("--symbol", default="MGC", help="output symbol name (Level_0)")
    parser.add_argument("--root", default=None, help="contract root prefix (default = symbol)")
    args = parser.parse_args()

    dbn_path = Path(args.dbn)
    if not dbn_path.is_absolute():
        dbn_path = ROOT / dbn_path
    root = args.root or args.symbol

    df = extract_front_month(dbn_path, root)
    write_duckdb(df, args.symbol, cfg)


if __name__ == "__main__":
    main()
