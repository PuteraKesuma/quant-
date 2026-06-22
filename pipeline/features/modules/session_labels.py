"""Tag each bar with its trading session."""
import pandas as pd

SESSIONS = {
    "frankfurt": ("07:00", "08:00"),
    "london":    ("08:00", "17:00"),
    "new_york":  ("13:30", "22:00"),
    "asian":     ("00:00", "07:00"),
}


def label_sessions(df: pd.DataFrame) -> pd.Series:
    """Return a Series of session labels aligned to df index (UTC)."""
    labels = pd.Series("off", index=df.index, name="session")
    for name, (start, end) in SESSIONS.items():
        sh, sm = map(int, start.split(":"))
        eh, em = map(int, end.split(":"))
        mask = (
            (df.index.hour * 60 + df.index.minute >= sh * 60 + sm) &
            (df.index.hour * 60 + df.index.minute <  eh * 60 + em)
        )
        labels[mask] = name
    return labels
