"""Time-based features: hour, day of week, minutes since session open."""
import pandas as pd
import numpy as np


def time_features(df: pd.DataFrame) -> pd.DataFrame:
    idx = df.index
    feats = pd.DataFrame(index=idx)
    feats["hour"]       = idx.hour
    feats["minute"]     = idx.minute
    feats["dow"]        = idx.dayofweek          # 0=Mon, 4=Fri
    feats["hour_sin"]   = np.sin(2 * np.pi * idx.hour / 24)
    feats["hour_cos"]   = np.cos(2 * np.pi * idx.hour / 24)
    feats["dow_sin"]    = np.sin(2 * np.pi * idx.dayofweek / 5)
    feats["dow_cos"]    = np.cos(2 * np.pi * idx.dayofweek / 5)
    return feats
