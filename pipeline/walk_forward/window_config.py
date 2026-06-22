"""Generate walk-forward windows from config."""
from dataclasses import dataclass
import pandas as pd
import yaml
from pathlib import Path


@dataclass
class WFWindow:
    index:       int
    train_start: pd.Timestamp
    train_end:   pd.Timestamp
    test_start:  pd.Timestamp
    test_end:    pd.Timestamp


def generate_windows(start: str, end: str, cfg: dict) -> list[WFWindow]:
    wf = cfg["walk_forward"]
    in_months  = wf["in_sample_months"]
    out_months = wf["out_sample_months"]
    step       = wf["step_months"]

    windows = []
    cursor = pd.Timestamp(start, tz="UTC")
    total_end = pd.Timestamp(end, tz="UTC")
    i = 0

    while True:
        train_start = cursor
        train_end   = cursor + pd.DateOffset(months=in_months)
        test_start  = train_end
        test_end    = test_start + pd.DateOffset(months=out_months)

        if test_end > total_end:
            break

        windows.append(WFWindow(i, train_start, train_end, test_start, test_end))
        cursor += pd.DateOffset(months=step)
        i += 1

    return windows
