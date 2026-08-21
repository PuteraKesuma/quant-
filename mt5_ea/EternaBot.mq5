//+------------------------------------------------------------------+
//| EternaBot.mq5 - eterna_xau ported to MQL5 for Strategy Tester use |
//|                                                                  |
//| WHY THIS EXISTS                                                  |
//| eterna's numbers come from a Python backtest (bar-close fills, a  |
//| flat $0.50/round-trip cost, no slippage) while Semi Marti's come  |
//| from the MT5 Strategy Tester (every tick, real spread, real fill  |
//| sequence). Any "combined portfolio" figure therefore mixes two    |
//| fidelity levels. This port lets eterna run through the SAME       |
//| engine, so the two can finally be compared like for like -- and   |
//| so the Python model itself can be checked against real execution. |
//|                                                                  |
//| PARITY IS THE WHOLE POINT                                         |
//| A port that quietly differs would produce confident numbers for a |
//| strategy we do not run. So this mirrors                           |
//| pipeline/live/signal.py::EternaStrategy line by line:             |
//|   - Supertrend(ATR16, 1.8) = entry; Supertrend(ATR16, 3.8) = gate |
//|   - act only on a CLOSED H1 bar, one decision per bar             |
//|   - enter when the entry Supertrend FLIPS and the trend one AGREES|
//|   - SL = extreme of the last `struct_bars` closed bars (incl. the |
//|     signal bar, which is what the Python slice h_c[-16:] does)    |
//|   - TP = tp_ratio x that distance; reject if distance < min_sl_dist|
//|   - an opposite flip closes the position early                    |
//|   - one position at a time                                        |
//| Validation target: 2026-01-01..2026-08-17 the Python run produced |
//| 43 trades / +474.26 net at 0.01 lot. Trade count is the sharper   |
//| check of the two -- P&L can drift on spread alone, but a          |
//| different trade count means different SIGNALS.                    |
//+------------------------------------------------------------------+
#property strict

#include <Trade\Trade.mqh>

input double InpLot            = 0.01;
input int    InpATRPeriod      = 16;     // eterna atr_period
input double InpMultEntry      = 1.8;    // eterna mult_entry
input double InpMultTrend      = 3.8;    // eterna mult_trend
input int    InpStructBars     = 16;     // tied to atr_period, as EA line 259 does
input double InpTPRatio        = 4.0;    // eterna tp_ratio
input double InpMinSLDist      = 0.30;   // eterna min_sl_dist
// CLAMP jarak stop ke nilai ini (0 = mati). Beda dari InpRiskCapUSD: cap MELEWATI
// trade yang stop strukturnya terlalu lebar, clamp tetap MASUK tapi memendekkan
// stopnya. TP tetap InpTPRatio x jarak yang DIPAKAI, jadi clamp 30 + TP 1:4 -> TP 120.
// Bentuk SKIP berbahaya di regime volatil: di 2026 (emas ~4500) tidak ada satu pun
// stop struktur di bawah 30, jadi cap 30 menghasilkan NOL trade sepanjang tahun.
input double InpMaxSLDist      = 0.0;    // 0 = off; mis. 30.0 untuk clamp $30
input double InpRiskCapUSD     = 0.0;    // 0 = off (the Python reference run had no cap)
input int    InpMagic          = 920627; // same magic as the live slot
input int    InpHistBars       = 600;    // H1 bars pulled for the indicator state
input bool   InpDebug          = false;

CTrade   trade;
datetime g_last_bar = 0;

//+------------------------------------------------------------------+
//| WILDER ATR -- computed here on purpose, NOT via iATR.             |
//|                                                                  |
//| MT5's built-in iATR is a SIMPLE moving average of True Range, not |
//| Wilder smoothing. (MT4's was Wilder; MT5 changed it.) Measured on |
//| 182 identical bars, iATR matched SMA(TR,16) to 0.0005 while it    |
//| differed from Wilder by up to 9.64 on an ATR near 30.             |
//|                                                                  |
//| The LIVE brain uses Wilder -- EternaStrategy._atr is              |
//| tr.ewm(alpha=1/n, adjust=False). So an EA built on iATR is a      |
//| DIFFERENT STRATEGY from the one that trades the account, and its  |
//| backtests describe something we do not run. That is exactly what  |
//| happened: with the same bars and identical OHLC, the trend gate   |
//| pointed -1 in the EA and +1 in the brain at 2026-02-16 06:00, and |
//| the EA took 110 trades in 2023 where the brain's logic takes 71.  |
//|                                                                  |
//| Wilder: atr[n-1] = mean(tr[0..n-1]); atr[i] = (atr[i-1]*(n-1)+tr[i])/n
//| Seeding with the SMA of the first n matches pandas' ewm closely   |
//| after a few hundred bars, and both converge long before the bars  |
//| this EA actually trades on.                                       |
//+------------------------------------------------------------------+
bool WilderATR(const double &high[], const double &low[], const double &close[],
               const int n, const int period, double &atr[])
  {
   if(n < period + 1 || period < 1)
      return false;
   ArrayResize(atr, n);

   double tr[];
   ArrayResize(tr, n);
   tr[0] = high[0] - low[0];                       // no previous close for bar 0
   for(int i = 1; i < n; i++)
     {
      double hl = high[i] - low[i];
      double hc = MathAbs(high[i] - close[i-1]);
      double lc = MathAbs(low[i]  - close[i-1]);
      tr[i] = MathMax(hl, MathMax(hc, lc));
     }

   double sum = 0.0;
   for(int i = 0; i < period; i++)
     {
      sum += tr[i];
      atr[i] = 0.0;                                // not enough history yet
     }
   atr[period-1] = sum / period;                   // seed
   for(int i = period; i < n; i++)
      atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period;
   return true;
  }

//+------------------------------------------------------------------+
//| Supertrend direction, identical to EternaStrategy._supertrend     |
//| Returns +1 / -1 per bar into dir[]; dir[n-1] is the newest.       |
//+------------------------------------------------------------------+
bool SupertrendDirs(const double &high[], const double &low[], const double &close[],
                    const double &atrBuf[], const int n, const double mult, int &dir[])
  {
   if(n < 3)
      return false;
   ArrayResize(dir, n);
   double fu = 0.0, fl = 0.0;
   bool started = false;
   int  d = 1;
   for(int i = 0; i < n; i++)
     {
      double a = atrBuf[i];
      if(a <= 0.0 || !MathIsValidNumber(a) || i == 0)
        { dir[i] = d; continue; }
      double hl2 = (high[i] + low[i]) / 2.0;
      double bu  = hl2 + mult * a;
      double bl  = hl2 - mult * a;
      if(!started)
        { fu = bu; fl = bl; started = true; dir[i] = d; continue; }
      // final bands: carry forward unless the new band is tighter, or the
      // previous close broke through -- exactly the Python condition
      fu = (bu < fu || close[i-1] > fu) ? bu : fu;
      fl = (bl > fl || close[i-1] < fl) ? bl : fl;
      if(close[i] > fu)      d = 1;
      else if(close[i] < fl) d = -1;
      dir[i] = d;
     }
   return started;
  }

int MyPositionType()   // -1 none, 0 buy, 1 sell
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk == 0 || !PositionSelectByTicket(tk))
         continue;
      if(PositionGetInteger(POSITION_MAGIC) == InpMagic &&
         PositionGetString(POSITION_SYMBOL) == _Symbol)
         return (int)PositionGetInteger(POSITION_TYPE);
     }
   return -1;
  }

void CloseMine()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk == 0 || !PositionSelectByTicket(tk))
         continue;
      if(PositionGetInteger(POSITION_MAGIC) == InpMagic &&
         PositionGetString(POSITION_SYMBOL) == _Symbol)
         trade.PositionClose(tk);
     }
  }

int OnInit()
  {
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(50);
   // Tidak ada handle indikator: ATR dihitung sendiri (Wilder) supaya identik
   // dengan brain live. iATR bawaan MT5 adalah SMA -- lihat WilderATR().
   PrintFormat("EternaBot init: ATR%d(Wilder) entry x%.1f trend x%.1f TP1:%.0f lot %.2f",
               InpATRPeriod, InpMultEntry, InpMultTrend, InpTPRatio, InpLot);
   return INIT_SUCCEEDED;
  }

void OnTick()
  {
   // one decision per CLOSED H1 bar, mirroring the brain's bar-cache guard
   datetime bt = iTime(_Symbol, PERIOD_H1, 1);
   if(bt == 0 || bt == g_last_bar)
      return;

   int n = InpHistBars;
   double high[], low[], close[], atrBuf[];
   ArraySetAsSeries(high, false);
   ArraySetAsSeries(low, false);
   ArraySetAsSeries(close, false);
   ArraySetAsSeries(atrBuf, false);

   // shift 1 => CLOSED bars only, never the forming one
   if(CopyHigh (_Symbol, PERIOD_H1, 1, n, high)  != n) return;
   if(CopyLow  (_Symbol, PERIOD_H1, 1, n, low)   != n) return;
   if(CopyClose(_Symbol, PERIOD_H1, 1, n, close) != n) return;

   // ATR dihitung SENDIRI dengan smoothing Wilder, supaya sama dengan brain live.
   // iATR bawaan MT5 adalah SMA dari True Range -- lihat catatan di WilderATR().
   if(!WilderATR(high, low, close, n, InpATRPeriod, atrBuf)) return;

   int de[], dt[];
   if(!SupertrendDirs(high, low, close, atrBuf, n, InpMultEntry, de)) return;
   if(!SupertrendDirs(high, low, close, atrBuf, n, InpMultTrend, dt)) return;

   g_last_bar = bt;

   bool flipped = (de[n-1] != de[n-2]);
   int  s       = de[n-1];
   bool aligned = (dt[n-1] == s);
   int  want    = (s == 1) ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
   int  held    = MyPositionType();

   // Jejak per-bar untuk membandingkan EA vs harness Python bar demi bar.
   // Dipakai untuk melacak selisih jumlah trade (EA 110 vs Python 71 di 2023).
   // Hanya untuk jendela sempit -- di setahun penuh log-nya jadi raksasa.
   if(InpDebug)
      PrintFormat("BAR %s O=%.2f H=%.2f L=%.2f C=%.2f atr=%.3f de=%d dePrev=%d dt=%d flip=%d align=%d held=%d",
                  TimeToString(bt, TIME_DATE | TIME_MINUTES),
                  iOpen(_Symbol, PERIOD_H1, 1), high[n-1], low[n-1], close[n-1],
                  atrBuf[n-1], de[n-1], de[n-2], dt[n-1],
                  (int)flipped, (int)aligned, held);

   // opposite flip closes an open position early (same as the brain)
   if(held >= 0)
     {
      if(flipped && want != held)
        {
         if(InpDebug) Print("opposite flip -> close");
         CloseMine();
        }
      return;                       // otherwise hold; broker SL/TP does the exit
     }

   if(!flipped || !aligned)
      return;

   double px = close[n-1];

   // SL = extreme of the last InpStructBars CLOSED bars (slice includes the
   // signal bar, matching the Python h_c.iloc[-struct_bars:])
   int lo = MathMax(0, n - InpStructBars);
   double raw = (s == 1) ? low[lo] : high[lo];
   for(int i = lo; i < n; i++)
     {
      if(s == 1) raw = MathMin(raw, low[i]);
      else       raw = MathMax(raw, high[i]);
     }

   double dist = MathAbs(px - raw);
   if(dist < InpMinSLDist)
     {
      if(InpDebug) PrintFormat("structure stop too tight (%.2f) -> skip", dist);
      return;
     }

   // Clamp SETELAH uji min-dist dan SEBELUM TP dihitung, supaya TP mengacu pada
   // jarak yang benar-benar dipakai (clamp 30 + TP 1:4 -> TP 120).
   if(InpMaxSLDist > 0.0 && dist > InpMaxSLDist)
     {
      if(InpDebug) PrintFormat("clamp stop %.2f -> %.2f", dist, InpMaxSLDist);
      dist = InpMaxSLDist;
     }

   double sl = (s == 1) ? px - dist : px + dist;
   double tp = (s == 1) ? px + InpTPRatio * dist : px - InpTPRatio * dist;

   if(InpRiskCapUSD > 0.0)
     {
      double risk = dist * InpLot * SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
      if(risk > InpRiskCapUSD)
        {
         if(InpDebug) PrintFormat("risk cap: %.2f > %.2f -> skip", risk, InpRiskCapUSD);
         return;
        }
     }

   int dg = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   sl = NormalizeDouble(sl, dg);
   tp = NormalizeDouble(tp, dg);

   bool ok = (s == 1) ? trade.Buy(InpLot, _Symbol, 0.0, sl, tp, "eterna")
                      : trade.Sell(InpLot, _Symbol, 0.0, sl, tp, "eterna");
   if(!ok)
      PrintFormat("ERROR entry failed rc=%u %s", trade.ResultRetcode(), trade.ResultComment());
   else if(InpDebug)
      PrintFormat("%s @ %.2f sl=%.2f tp=%.2f dist=%.2f",
                  (s == 1 ? "BUY" : "SELL"), px, sl, tp, dist);
  }
//+------------------------------------------------------------------+
