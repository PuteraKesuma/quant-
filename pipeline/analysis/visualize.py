"""Equity curve, drawdown, and win-rate charts via plotly."""
from pathlib import Path
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from .metrics import equity_curve

ROOT = Path(__file__).parent.parent.parent


def plot_equity(symbol: str, mode: str = "backtest"):
    parquet = ROOT / "data" / "Level_2_Datamart" / mode / f"{symbol}_{mode}.parquet"
    df = pd.read_parquet(parquet)
    ec = equity_curve(df)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        subplot_titles=["Equity (R)", "Drawdown (R)"])
    fig.add_trace(go.Scatter(x=ec.index, y=ec.values, name="Equity"), row=1, col=1)
    dd = ec - ec.cummax()
    fig.add_trace(go.Scatter(x=dd.index, y=dd.values, fill="tozeroy",
                             name="Drawdown", line_color="red"), row=2, col=1)
    fig.update_layout(title=f"{symbol} {mode}", height=600)

    out = ROOT / "_DOC" / "_PRD" / f"{symbol}_{mode}_equity.html"
    fig.write_html(str(out))
    return fig
