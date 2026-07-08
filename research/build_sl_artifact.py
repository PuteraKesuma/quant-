"""Build a self-contained HTML page embedding the 6 SL-hit charts (base64) for viewing."""
import base64
import os

D = r"C:\Quant\_DOC\sl_analysis"
OUT = (r"C:\Users\ADMINI~1\AppData\Local\Temp\1\claude\C--Users-Administrator"
       r"\91e0ccf1-c993-48f2-8268-f1678ad108cb\scratchpad\sl_analysis.html")


def b64(name):
    with open(os.path.join(D, name), "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


golden = [
    ("golden_sl_1.png", "2026-02-11 13:35 UTC", "BUY", "-$44.76", "ADX 21",
     "Bought the dip in an uptrend, then price chopped sideways for ~25 hours pinned near entry — never reaching the far 3R target — before a sharp break down blew clean through the stop. The mild pullback the fade bought turned into a real down-move."),
    ("golden_sl_2.png", "2026-04-07 02:05 UTC", "SELL", "-$37.80", "ADX ~20",
     "Sold the bounce in a downtrend; price kept grinding the other way and tagged the stop. Fade caught on the wrong side of a move that had more in it."),
    ("golden_sl_3.png", "2026-03-30 14:20 UTC", "BUY", "-$34.70", "ADX ~19",
     "Same signature: fade a dip, dip deepens past the 3x-ATR stop. Capped at ~$35 by the ATR stop — this is death-by-small-cuts, the cost the 3R winners pay for."),
]
z = [
    ("z_sl_1.png", "2026-02-04 01:00 UTC", "LONG", "-$135.87", "S&R whipsaw",
     "Bought the Donchian breakout at ~5016 after a strong rally from 4780 — i.e. near the exhaustion of the up-move. Price gave a little more, then reversed hard down to 4810; the stop got hit on the way down. Z's largest class of loss: catching the tail of a move right before it turns."),
    ("z_sl_2.png", "2026-04-02 06:00 UTC", "SHORT", "-$114.53", "S&R whipsaw",
     "Shorted a downside channel break that reversed back up. A false breakdown — the break failed and ran the opposite way into the stop."),
    ("z_sl_3.png", "2026-03-06 19:00 UTC", "LONG", "-$109.41", "S&R whipsaw",
     "Long breakout that reversed. These few large whipsaws (-$109 to -$136 at 0.01 lot) are exactly why Z needs bigger capital and why its whipsaws are unpredictable at entry — winners and losers look identical when the position opens."),
]


def cards(items, cls):
    out = []
    for img, when, side, pnl, tag, note in items:
        sidecls = "buy" if side in ("BUY", "LONG") else "sell"
        out.append(f'''<figure class="shot">
      <div class="cap">
        <div class="meta"><span class="when">{when}</span>
          <span class="side {sidecls}">{side}</span>
          <span class="tag">{tag}</span>
          <span class="pnl">{pnl}</span></div>
      </div>
      <div class="imgwrap"><img src="{b64(img)}" alt="{side} stop-loss chart {when}" loading="lazy"></div>
      <figcaption>{note}</figcaption>
    </figure>''')
    return "\n".join(out)


html = f'''<title>Anatomy of a Stop-Loss — Golden &amp; Z</title>
<style>
:root {{
  --bg:#0d1117; --panel:#151b23; --panel2:#1b2330; --ink:#e6edf3; --muted:#8b98a8;
  --line:#26303c; --gold:#d9a441; --zblue:#5aa2e6; --loss:#ef5350; --up:#26a69a;
  --shadow:0 1px 0 #ffffff08, 0 8px 30px #0008;
}}
@media (prefers-color-scheme: light) {{
  :root {{ --bg:#f5f7fa; --panel:#ffffff; --panel2:#f0f3f7; --ink:#141a22; --muted:#5c6773;
    --line:#e2e8f0; --gold:#a9781f; --zblue:#2b6cb0; --loss:#c0392b; --up:#1a8f80;
    --shadow:0 1px 0 #fff, 0 6px 22px #0f172a12; }}
}}
:root[data-theme="dark"] {{ --bg:#0d1117; --panel:#151b23; --panel2:#1b2330; --ink:#e6edf3;
  --muted:#8b98a8; --line:#26303c; --gold:#d9a441; --zblue:#5aa2e6; --loss:#ef5350; --up:#26a69a;
  --shadow:0 1px 0 #ffffff08, 0 8px 30px #0008; }}
:root[data-theme="light"] {{ --bg:#f5f7fa; --panel:#ffffff; --panel2:#f0f3f7; --ink:#141a22;
  --muted:#5c6773; --line:#e2e8f0; --gold:#a9781f; --zblue:#2b6cb0; --loss:#c0392b; --up:#1a8f80;
  --shadow:0 1px 0 #fff, 0 6px 22px #0f172a12; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; line-height:1.55;
  -webkit-font-smoothing:antialiased; }}
.wrap {{ max-width:860px; margin:0 auto; padding:56px 22px 80px; }}
.mono {{ font-family:ui-monospace,"SF Mono","Cascadia Code",Menlo,monospace; font-variant-numeric:tabular-nums; }}
.eyebrow {{ font:600 12px/1 ui-monospace,monospace; letter-spacing:.18em; text-transform:uppercase;
  color:var(--muted); margin:0 0 14px; }}
h1 {{ font-size:clamp(28px,5vw,42px); line-height:1.05; margin:0 0 14px; letter-spacing:-.02em; text-wrap:balance; }}
.lede {{ font-size:18px; color:var(--muted); max-width:60ch; margin:0 0 34px; }}
.summary {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin:0 0 46px; }}
@media (max-width:620px) {{ .summary {{ grid-template-columns:1fr; }} }}
.pat {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:18px 18px 16px;
  box-shadow:var(--shadow); }}
.pat h3 {{ margin:0 0 6px; font-size:15px; display:flex; align-items:center; gap:9px; }}
.dot {{ width:9px; height:9px; border-radius:50%; display:inline-block; }}
.pat p {{ margin:0; font-size:14px; color:var(--muted); }}
.pat .verdict {{ margin-top:10px; font:600 12px/1.4 ui-monospace,monospace; color:var(--ink); }}
section {{ margin:0 0 40px; }}
.shead {{ display:flex; align-items:baseline; gap:12px; border-bottom:1px solid var(--line);
  padding-bottom:10px; margin-bottom:22px; }}
.shead h2 {{ margin:0; font-size:22px; letter-spacing:-.01em; }}
.shead .sub {{ font-size:13px; color:var(--muted); }}
.g-accent {{ color:var(--gold); }} .z-accent {{ color:var(--zblue); }}
.shot {{ margin:0 0 26px; background:var(--panel); border:1px solid var(--line); border-radius:12px;
  overflow:hidden; box-shadow:var(--shadow); }}
.cap {{ padding:13px 16px 11px; }}
.meta {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; font-family:ui-monospace,monospace;
  font-size:12.5px; }}
.when {{ color:var(--muted); }}
.side {{ font-weight:700; padding:2px 8px; border-radius:5px; font-size:11.5px; letter-spacing:.03em; }}
.side.buy {{ color:var(--up); background:color-mix(in srgb,var(--up) 15%,transparent); }}
.side.sell {{ color:var(--loss); background:color-mix(in srgb,var(--loss) 15%,transparent); }}
.tag {{ color:var(--muted); }}
.pnl {{ margin-left:auto; color:var(--loss); font-weight:700; font-variant-numeric:tabular-nums; }}
.imgwrap {{ overflow-x:auto; background:#fff; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }}
.imgwrap img {{ display:block; width:100%; min-width:640px; height:auto; }}
figcaption {{ padding:13px 16px 15px; font-size:14px; color:var(--muted); }}
.foot {{ margin-top:46px; padding:20px 20px; background:var(--panel2); border:1px solid var(--line);
  border-radius:12px; font-size:14px; color:var(--muted); }}
.foot strong {{ color:var(--ink); }}
</style>

<div class="wrap">
  <p class="eyebrow">Loss forensics &middot; XAU demo book</p>
  <h1>Anatomy of a stop-loss</h1>
  <p class="lede">The worst SL-hit trades for the two gold strategies, on the chart. Each strategy
  loses in one characteristic way &mdash; seeing it is the point: the losses aren't bugs, they're the
  price the edge pays.</p>

  <div class="summary">
    <div class="pat">
      <h3><span class="dot" style="background:var(--gold)"></span>Golden &mdash; the fade gets run over</h3>
      <p>Buys a dip / sells a bounce expecting reversion. It loses when the small move it faded turns
      into a real trend and runs past the 3&times;ATR stop.</p>
      <p class="verdict">Frequent, but SMALL &mdash; every loss capped ~$35&ndash;45.</p>
    </div>
    <div class="pat">
      <h3><span class="dot" style="background:var(--zblue)"></span>Z &mdash; the breakout whipsaws</h3>
      <p>Buys a channel breakout / sells a breakdown. It loses when the break is false &mdash; price
      reverses right after entry, near the exhaustion of the prior move.</p>
      <p class="verdict">Rare, but LARGE &mdash; $109&ndash;136 at 0.01 lot.</p>
    </div>
  </div>

  <section>
    <div class="shead"><h2>Golden <span class="g-accent">&mdash; fade run over</span></h2>
      <span class="sub">M5 &middot; 3&times;ATR stop &middot; 3R target</span></div>
    {cards(golden, "g")}
  </section>

  <section>
    <div class="shead"><h2>Z <span class="z-accent">&mdash; S&amp;R whipsaw</span></h2>
      <span class="sub">H1 Donchian stop-and-reverse</span></div>
    {cards(z, "z")}
  </section>

  <div class="foot">
    <strong>The takeaway is the contrast.</strong> Golden bleeds in many tiny cuts; Z bleeds in a few
    deep ones. They also lose on <em>different</em> days &mdash; that's the diversification that keeps
    the book green. And Z's whipsaws are <strong>unpredictable at entry</strong>: winners and losers
    look identical when the trade opens, so the mitigation is sizing + diversification, not a filter.
  </div>
</div>'''

with open(OUT, "w", encoding="utf-8") as f:
    f.write(html)
print("wrote", OUT, os.path.getsize(OUT) // 1024, "KB")
