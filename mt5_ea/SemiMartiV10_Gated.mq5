//+------------------------------------------------------------------+
//| EA: Semi_Marti_Cuan.mq5                                          |
//| Version: 10.0 - Deep Entry Limit Orders for Martingale Layers   |
//| Layer 1: gap * 1.5x depth  → BuyLimit / SellLimit               |
//| Layer 2: gap * 2.5x depth  (deeper discount zone)               |
//| Layer 3: gap * 4.0x depth  (maximum structural depth)           |
//| Only ONE pending limit allowed per side at a time               |
//+------------------------------------------------------------------+
#property copyright "Voyager Labs"
#property version   "10.00"
#property link      "https://t.me/smaugh6"
#property description "Optimized for XAUUSD"
#property strict
#include <Trade\Trade.mqh>
CTrade trade;

//--------------------- INPUTS -------------------------
input group "Indicator Parameters";
input int    InpRSIPeriod        = 250; //RSI Period
input int    InpMAPeriod         = 1; //MA Period

input double InpRSILevelHigh     = 80.0;
input double InpRSILevelMid      = 50.0;
input double InpRSILevelLow      = 20.0;

input int    InpMACDFast         = 5;
input int    InpMACDSlow         = 13;
input int    InpMACDSignal       = 9;
input int    InpMACDNormalizePeriod = 100;

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
input int    InpMASource         = 0;    // 0=PRICE,1=RSI,2=MACD_SIGNAL()
input int    InpMASmaPeriod      = 21;

input group "Trading Parameters";
input bool   InpUseMartingale    = true;
input double InpStartLot         = 0.01;
input double InpLotMultiplier    = 1.5;
input int    InpMaxLayers        = 3;
input double InpOrderGapPips     = 25.0;
input int    InpLayerStartBEP    = 3; // layer number when BEP scan starts
input double InpLayerBEPOffsetPips = 1.0; // offset in pips from BEP to trigger
input bool   InpLayerOncePerSeries = true; // trigger BEP only once per martingale series

input double InpGlobalTP_USD     = 25.0;
input double InpGlobalSL_USD     = 0;
input double InpDailyTargetUSD   = 50.0;

input bool   InpUseTrailingUSD   = true;
input double InpTrailingStartUSD = 10.0;
input double InpTrailingStepUSD  = 2.0;

input group "First Signal Dual Entry";
input double InpFirstEntry_TP_USD      = 10.0;  // TP for position #1 (fixed $10)
input double InpFirstEntry_Trail_USD   = 25.0;  // Trailing target for position #2 ($25)

input group "Deep Entry (Limit Order Layers)";
// Depth multiplier per layer — applied to InpOrderGapPips as the base unit.
// Layer 1 limit placed at: lastPrice - gap * Layer1DepthMult  (for BUY)
// Layer 2: gap * Layer2DepthMult, Layer 3: gap * Layer3DepthMult
input double InpLayer1DepthMult  = 1.5;  // Layer 1 depth multiplier (x gap)
input double InpLayer2DepthMult  = 2.5;  // Layer 2 depth multiplier (x gap)
input double InpLayer3DepthMult  = 4.0;  // Layer 3 depth multiplier (x gap)
// Expiry of pending limit orders in minutes (0 = no expiry / GTC)
input int    InpLimitExpiryMins  = 240;  // Limit order expiry in minutes (0=GTC)

input group "Time Filter";
input int    InpStartHour        = 9;
input int    InpEndHour          = 23;

input group "News Filter (MT5 Calendar)";
input bool   InpUseNewsFilter    = true;   // Enable/disable news filter
input int    InpNewsMinutesBefore = 60;    // Minutes BEFORE news to block entry
input int    InpNewsMinutesAfter  = 30;    // Minutes AFTER news to block entry
// Impact level to block: 1=Low, 2=Medium, 3=High (blocks this level AND above)
input int    InpNewsImpactLevel  = 2;      // 2=block Medium+High, 3=block High only
// Country filter: "USD" blocks USD news, "XAU" blocks gold-specific, "" = all
input string InpNewsCountry      = "USD";  // Country code (USD = US news)

input group "Confirmation";
input bool   InpRequireBreakConfirm = true; // require pullback + re-break confirmation before opening
// max OnTick cycles to wait for confirmation before reset (0 = wait indefinitely, no timeout)
input int    InpConfirmMaxTicks = 0;

input group "UI";
input bool   InpShowIndicators   = true;
input int    InpDrawBars         = 50;
input color  InpColorRSI         = clrDodgerBlue;
input color  InpColorSMA         = clrOrange;
input color  InpColorMACDNorm    = clrMediumVioletRed;

input string InpEAName           = "VYG - MRM";
input string InpOrderComment     = "WA HANYA INI 081227853434";
input int    InpMagicNumber      = 20250822;
input bool   InpDebug            = true;

// New: signal mode - 0=AND,1=OR,2=SMA only,3=MACD only
input int    InpSignalMode       = 2;

//=== REGIME GATE (added 2026-08-20) =================================
// This EA fades; it bleeds when price trends. Measured over three MT5 tester
// years (2023/2024/2026 XAUUSD M5, TP40/SL75, cap 0.02), splitting its baskets
// by whether the two Supertrends of the eterna slot AGREE (a confirmed trend)
// or CONFLICT (no clear trend) gave:
//        agree (trend)  : 2023 -306.43 | 2024  +97.39 | 2026 +240.78  => +31.74
//        conflict (chop): 2023  -69.10 | 2024  +55.33 | 2026 +719.19  => +705.42
// i.e. essentially all of the edge sits in the no-trend regime, and the worst
// year's loss shrinks ~4.4x. Gate ON = only start a NEW series when the two
// Supertrends CONFLICT. Layer additions to an already-open basket are never
// gated -- interrupting a live basket would break the recovery behaviour that
// produces the high win rate (proved by the MaxLayers=2 test, which raised the
// win rate to 81.9% but tripled the average loss and made 2023 worse).
//
// The maths is computed here rather than fetched from the brain on purpose:
// the Strategy Tester cannot make WebRequest calls, so a brain-hosted gate
// would be impossible to backtest -- and shipping an unverified change is the
// exact failure this whole exercise exists to avoid. Same indicator, same
// parameters as pipeline/live/signal.py::EternaStrategy.
input bool   InpUseRegimeGate    = true;   // only open new series when NO clear trend
input ENUM_TIMEFRAMES InpGateTF  = PERIOD_H1;
input int    InpGateATRPeriod    = 16;     // eterna atr_period
input double InpGateMultEntry    = 1.8;    // eterna mult_entry
input double InpGateMultTrend    = 3.8;    // eterna mult_trend
input int    InpGateBars         = 400;    // history used to settle the Supertrend state

//--------------------- GLOBALS ------------------------
double lastRsiMA = 0.0;
datetime lastResetDaily = 0;
double dailyProfitAcc = 0.0;

ulong trackedTickets[];
double trackedPeaks[];

long chart_id_global = 0;
int gi_indicator_window = 1;

string OBJ_PREF_RSI = "EA_RSI_LINE_";
string OBJ_PREF_SMA = "EA_SMA_LINE_";
string OBJ_PREF_MACD = "EA_MACD_LINE_";
string OBJ_PREF_LVL = "EA_LVL_";

double equityPeak = 0.0;
bool trailingActive = false;    // true setelah equity-balance mencapai InpTrailingStartUSD

// Confirmation state for conservative entries (pullback + re-break)
bool waitSellConfirm = false;
bool sellPulled = false;
int  waitSellTicks = 0;

bool waitBuyConfirm = false;
bool buyPulled = false;
int  waitBuyTicks = 0;

// track BEP-trigger
bool layerBEPTriggered = false;

// Dual-entry tracking (first signal only)
ulong g_tp1_ticket   = 0;   // position #1: close at InpFirstEntry_TP_USD
ulong g_trail_ticket = 0;   // position #2: trailing at InpFirstEntry_Trail_USD
double g_trail_entry_balance = 0.0;  // balance snapshot when dual entry opened
bool   g_trail_active = false;       // trailing for position #2 activated
double g_trail_peak_profit = 0.0;    // running profit peak for position #2 trailing

// Deep Entry — pending limit order tracking per side
// Only one limit order per side is allowed at a time.
ulong  g_pending_buy  = 0;   // ticket of active BuyLimit for martingale
ulong  g_pending_sell = 0;   // ticket of active SellLimit for martingale

//+------------------------------------------------------------------+
//| Initialization                                                   |
//+------------------------------------------------------------------+


//---------------------- Helper: EMA from prices --------------------
double EMA_from_prices(const double &prices[], int barsCount, int period)
  {
   if(period<=1)
      return prices[0];
   if(barsCount < period)
      return prices[0];

   double pRev[];
   ArrayResize(pRev, barsCount);
   for(int i=0;i<barsCount;i++)
      pRev[i] = prices[barsCount-1 - i];

   double sum = 0.0;
   for(int i=0;i<period;i++)
      sum += pRev[i];
   double ema = sum / period;
   double k = 2.0 / (period + 1.0);
   for(int i=period;i<barsCount;i++)
      ema = pRev[i]*k + ema*(1.0 - k);
   return ema;
  }

//---------------- Compute MACD signal raw and independent SMA-----------
bool Compute_MACD_and_SMA_Normalized(int fast,int slow,int signal,int smaPeriod,int normalizePeriod,int barsToCompute,
                                     double &macdSignalNormSeries[], double &smaNormSeries[], const double &rsiProvided[], int rsiLen)
  {
   if(fast<=0 || slow<=0 || slow<=fast || signal<=0 || smaPeriod<=0 || normalizePeriod<=0)
      return false;

   int need = slow + normalizePeriod + barsToCompute + signal + smaPeriod + 10;
   double closes[];
   ArrayResize(closes, need);
   ArraySetAsSeries(closes, true);
   int copied = CopyClose(_Symbol, _Period, 1, need, closes);
   if(copied <= 0)
      return false;
   ArrayResize(closes, copied);

   int macdCount = MathMin(barsToCompute + normalizePeriod + signal + smaPeriod, copied-1);
   if(macdCount<=0)
      return false;

   double tmpMain[];
   ArrayResize(tmpMain, macdCount);
   ArraySetAsSeries(tmpMain, true);
   for(int i=0;i<macdCount;i++)
     {
      int avail = copied - i;
      if(avail <= 0)
        {
         tmpMain[i] = 0.0;
         continue;
        }
      double sub[];
      ArrayResize(sub, avail);
      ArraySetAsSeries(sub, true);
      for(int j=0;j<avail;j++)
         sub[j] = closes[j + i];
      double ef = EMA_from_prices(sub, avail, fast);
      double es = EMA_from_prices(sub, avail, slow);
      tmpMain[i] = ef - es;
     }

   double signalRaw[];
   ArrayResize(signalRaw, macdCount);
   ArraySetAsSeries(signalRaw, true);
   for(int b=0;b<macdCount;b++)
     {
      double sum=0.0;
      int cnt=0;
      for(int s=0;s<signal;s++)
        {
         int pos = b + s;
         if(pos >= ArraySize(tmpMain))
            break;
         sum += tmpMain[pos];
         cnt++;
        }
      signalRaw[b] = (cnt>0) ? (sum / cnt) : tmpMain[b];
     }

   double sourceRaw[];
   ArrayResize(sourceRaw, macdCount);
   ArraySetAsSeries(sourceRaw, true);

   if(InpMASource == 0)
     {
      for(int b=0;b<macdCount;b++)
         sourceRaw[b] = closes[b];
     }
   else
      if(InpMASource == 1)
        {
         if(rsiLen >= macdCount)
           {
            for(int b=0;b<macdCount;b++)
               sourceRaw[b] = rsiProvided[b];
           }
         else
           {
            for(int b=0;b<macdCount;b++)
              {
               if(b + InpRSIPeriod >= copied)
                 {
                  sourceRaw[b] = 50.0;
                  continue;
                 }
               double gain=0.0, loss=0.0;
               for(int j=1;j<=InpRSIPeriod;j++)
                 {
                  double change = closes[j-1 + b] - closes[j + b];
                  if(change>0)
                     gain += change;
                  else
                     loss += -change;
                 }
               double avgG = gain / InpRSIPeriod;
               double avgL = loss / InpRSIPeriod;
               double rsi;
               if(avgL==0.0 && avgG==0.0)
                  rsi = 50.0;
               else
                  if(avgL==0.0)
                     rsi = 100.0;
                  else
                    {
                     double rs = avgG/avgL;
                     rsi = 100.0 - (100.0/(1.0 + rs));
                    }
               sourceRaw[b] = rsi;
              }
           }
        }
      else
        {
         for(int b=0;b<macdCount;b++)
            sourceRaw[b] = signalRaw[b];
        }

   double smaRaw[];
   ArrayResize(smaRaw, macdCount);
   ArraySetAsSeries(smaRaw, true);
   for(int b=0;b<macdCount;b++)
     {
      double sum=0.0;
      int cnt=0;
      for(int m=0;m<smaPeriod;m++)
        {
         int pos = b + m;
         if(pos >= ArraySize(sourceRaw))
            break;
         sum += sourceRaw[pos];
         cnt++;
        }
      smaRaw[b] = (cnt>0) ? (sum/cnt) : sourceRaw[b];
     }

   for(int b=0;b<barsToCompute;b++)
     {
      double mnSig = DBL_MAX, mxSig = -DBL_MAX;
      for(int w=0; w<normalizePeriod; w++)
        {
         int pos = b + w;
         if(pos >= ArraySize(signalRaw))
            break;
         double v = signalRaw[pos];
         if(v < mnSig)
            mnSig = v;
         if(v > mxSig)
            mxSig = v;
        }
      if(mnSig==DBL_MAX)
        {
         mnSig = 0.0;
         mxSig = 0.0;
        }
      double rawVal = signalRaw[b];
      double normRaw = 50.0;
      if(mxSig - mnSig != 0.0)
         normRaw = (rawVal - mnSig) / (mxSig - mnSig) * 100.0;
      if(normRaw < 0.0)
         normRaw = 0.0;
      if(normRaw > 100.0)
         normRaw = 100.0;
      macdSignalNormSeries[b] = normRaw;

      if(InpMASource == 1)
        {
         double val = smaRaw[b];
         if(val < 0.0)
            val = 0.0;
         if(val > 100.0)
            val = 100.0;
         smaNormSeries[b] = val;
        }
      else
        {
         double mnS = DBL_MAX, mxS = -DBL_MAX;
         for(int w=0; w<normalizePeriod; w++)
           {
            int pos = b + w;
            if(pos >= ArraySize(smaRaw))
               break;
            double v = smaRaw[pos];
            if(v < mnS)
               mnS = v;
            if(v > mxS)
               mxS = v;
           }
         if(mnS==DBL_MAX)
           {
            mnS = 0.0;
            mxS = 0.0;
           }
         double sval = smaRaw[b];
         double normS = 50.0;
         if(mxS - mnS != 0.0)
            normS = (sval - mnS) / (mxS - mnS) * 100.0;
         if(normS < 0.0)
            normS = 0.0;
         if(normS > 100.0)
            normS = 100.0;
         smaNormSeries[b] = normS;
        }
     }

   return true;
  }

//---------------------- Drawing utilities ---------------------------
void DeleteObjectsWithPrefix(const string prefix)
  {
   long cid = chart_id_global;
   int total = ObjectsTotal(0);
   for(int i=total-1;i>=0;i--)
     {
      string name = ObjectName(0,i);
      if(StringFind(name, prefix) == 0)
         ObjectDelete(cid, name);
     }
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
void DrawSeriesAsTrends(const double &values[], int count, const string prefix, color col)
  {
   if(!InpShowIndicators)
      return;
   DeleteObjectsWithPrefix(prefix);
   long cid = chart_id_global;
   int drawCount = MathMax(0, count-1);
   for(int i=0;i<drawCount;i++)
     {
      datetime t1 = (datetime) iTime(_Symbol,_Period, i+1);
      datetime t2 = (datetime) iTime(_Symbol,_Period, i+2);
      if(t1==0 || t2==0)
         continue;
      double p1 = values[i];
      double p2 = values[i+1];
      string nm = prefix + IntegerToString(i);
      if(!ObjectCreate(cid, nm, OBJ_TREND, gi_indicator_window, t1, p1, t2, p2))
        {
         continue;
        }
      ObjectSetInteger(cid, nm, OBJPROP_COLOR, (int)col);
      ObjectSetInteger(cid, nm, OBJPROP_WIDTH, 1);
      ObjectSetInteger(cid, nm, OBJPROP_STYLE, STYLE_SOLID);
      ObjectSetInteger(cid, nm, OBJPROP_SELECTABLE, false);
     }
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
void DrawLevelLine(const string name, double level, color col)
  {
   if(!InpShowIndicators)
      return;
   long cid = chart_id_global;
   if(ObjectFind(cid, name) != -1)
      ObjectDelete(cid, name);
   if(!ObjectCreate(cid, name, OBJ_HLINE, gi_indicator_window, 0, level))
      return;
   ObjectSetInteger(cid, name, OBJPROP_COLOR, (int)col);
   ObjectSetInteger(cid, name, OBJPROP_WIDTH, 1);
   ObjectSetInteger(cid, name, OBJPROP_STYLE, STYLE_DOT);
  }

//------------------- Auto add built-in RSI for sub window ---
void AutoAddBuiltInRSI()
  {
   if(!InpShowIndicators)
      return;
   long cid = ChartID();
   chart_id_global = cid;
   int handle = iRSI(_Symbol, _Period, InpRSIPeriod, PRICE_CLOSE);
   if(handle==INVALID_HANDLE)
     {
      if(InpDebug)
         PrintFormat("AutoAddRSI failed handle %d", GetLastError());
      return;
     }
   if(ChartIndicatorAdd(cid, gi_indicator_window, handle))
     {
      IndicatorRelease(handle);
      if(InpDebug)
         Print("AutoAddRSI: OK");
     }
   else
     {
      IndicatorRelease(handle);
      if(InpDebug)
         PrintFormat("AutoAddRSI fail ChartIndicatorAdd %d", GetLastError());
     }
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
void AutoRemoveBuiltInRSI()
  {
   if(!InpShowIndicators)
      return;
   string sn = "RSI";
   if(ChartIndicatorDelete(chart_id_global, gi_indicator_window, sn))
     { if(InpDebug) Print("AutoRemoveRSI: removed."); }
  }

//---------------------- Order / Martingale logic --------------------
int RequiredHistory()
  {
   int req1 = InpMACDSlow + InpMACDSignal + InpMACDNormalizePeriod + 10;
   int req2 = InpRSIPeriod + InpMASmaPeriod + 5;
   return MathMax(req1, req2);
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
int CountOpenPositions()
  {
   int count=0;
   int total = (int)PositionsTotal();
   for(int i=0;i<total;i++)
     {
      ulong t = PositionGetTicket(i);
      if(t==0)
         continue;
      if(!PositionSelectByTicket(t))
         continue;
      long magic = (long)PositionGetInteger(POSITION_MAGIC);
      string sym = PositionGetString(POSITION_SYMBOL);
      if(magic!=InpMagicNumber)
         continue;
      if(sym==_Symbol)
         count++;
     }
   return count;
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
bool GetLastPositionByOrderType(const ENUM_ORDER_TYPE orderType, ulong &out_ticket, double &out_lot, double &out_price, double &out_profit, datetime &out_time)
  {
   out_ticket = 0;
   out_lot=0;
   out_price=0;
   out_profit=0;
   out_time=0;
   int total = (int)PositionsTotal();
   for(int i=0;i<total;i++)
     {
      ulong t = PositionGetTicket(i);
      if(t==0)
         continue;
      if(!PositionSelectByTicket(t))
         continue;
      long magic = (long)PositionGetInteger(POSITION_MAGIC);
      if(magic!=InpMagicNumber)
         continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      if(sym!=_Symbol)
         continue;
      long ptype = (long)PositionGetInteger(POSITION_TYPE);
      long want = (orderType==ORDER_TYPE_BUY) ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
      if(ptype != want)
         continue;
      datetime tt = (datetime)PositionGetInteger(POSITION_TIME);
      if((int)tt > (int)out_time)
        {
         out_time = tt;
         out_ticket = t;
         out_lot = PositionGetDouble(POSITION_VOLUME);
         out_price = PositionGetDouble(POSITION_PRICE_OPEN);
         out_profit = PositionGetDouble(POSITION_PROFIT);
        }
     }
   return (out_ticket!=0);
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
double NormalizeLot(double lot)
  {
   double minLot = SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double step   = SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(minLot<=0 || step<=0)
      return lot;
   double n = MathFloor((lot - minLot)/step + 0.5);
   double res = minLot + n*step;
   if(res<minLot)
      res = minLot;
   if(res>maxLot)
      res = maxLot;
   return res;
  }

double MaxLotAllowed() { return SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX); }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
void OpenOrder(const ENUM_ORDER_TYPE type, double lot)
  {
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(50);
   bool res=false;
   if(type==ORDER_TYPE_BUY)
      res = trade.Buy(lot, NULL, 0.0, 0.0, 0.0, InpOrderComment);
   else
      res = trade.Sell(lot, NULL, 0.0, 0.0, 0.0, InpOrderComment);

   if(!res)
     {
      uint rc = trade.ResultRetcode();
      string rcmsg = trade.ResultComment();
      int gl = GetLastError();
      PrintFormat("DIAG ERROR: Order failed rc=%u comment=%s GetLastError=%d", rc, rcmsg, gl);
     }
   else
     {
      ulong ticket = trade.ResultOrder();
      PrintFormat("Order opened: type=%s lot=%.2f ticket=%I64u", (type==ORDER_TYPE_BUY?"BUY":"SELL"), lot, ticket);
      if(ticket!=0 && FindTicketIndex(ticket)==-1)
        {
         int newSize = ArraySize(trackedTickets)+1;
         ArrayResize(trackedTickets, newSize);
         ArrayResize(trackedPeaks, newSize);
         trackedTickets[newSize-1] = ticket;
         trackedPeaks[newSize-1] = 0.0;
        }
     }
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
int FindTicketIndex(ulong ticket)
  {
   for(int i=0;i<ArraySize(trackedTickets);i++)
      if(trackedTickets[i]==ticket)
         return i;
   return -1;
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
void RemoveTicketIndex(int idx)
  {
   int n = ArraySize(trackedTickets);
   if(idx<0 || idx>=n)
      return;
   for(int i=idx;i<n-1;i++)
     {
      trackedTickets[i]=trackedTickets[i+1];
      trackedPeaks[i]=trackedPeaks[i+1];
     }
   ArrayResize(trackedTickets, n-1);
   ArrayResize(trackedPeaks, n-1);
  }

//---------------------- GLOBAL USD TRAILING (close-all) ----------------
void ManageGlobalTrailing()
  {
// trailing disabled if user turned it off or start threshold is <= 0
   if(!InpUseTrailingUSD || InpTrailingStartUSD <= 0.0)
     {
      // make sure to not carry previous state if disabled
      trailingActive = false;
      equityPeak = 0.0;
      return;
     }

   // Basket EA INI saja -- bukan equity akun. Lihat catatan di MyFloatingPnL().
   // Sebelum perbaikan, trailing ini menyala karena floating ETERNA dan menutup
   // posisi EA ini; terekam live 2026-08-21 saat EA ini tidak punya posisi.
   int    legs = 0;
   double runningProfit = MyFloatingPnL(legs);
   double equity = runningProfit;          // puncak diukur pada P&L basket sendiri

   // PENTING: saat sebuah leg DITUTUP, untungnya pindah dari floating ke balance,
   // jadi runningProfit turun tanpa ada yang memburuk. Kode lama memakai
   // ACCOUNT_EQUITY yang sudah memuat realized, jadi kebal terhadap ini. Versi
   // floating-saja TIDAK kebal: basket 2 leg dengan floating +12, lalu leg #1 kena
   // TP tetap $10 -> floating jadi +2 -> drop 10 >= step 2 -> SELURUH basket
   // ditutup paksa. Padahal TP $10 di leg #1 itu memang desainnya dan terjadi
   // terus-menerus. Maka: begitu jumlah leg berkurang, puncak di-baseline ulang
   // ke level sekarang, sehingga trailing hanya menjaga sisa eksposur yang MASIH
   // terbuka -- yang memang tujuannya.
   static int s_legs = 0;
   if(legs < s_legs)
      equityPeak = runningProfit;
   s_legs = legs;

   if(!trailingActive)
     {
      if(runningProfit >= InpTrailingStartUSD)
        {
         trailingActive = true;
         equityPeak = equity;
         if(InpDebug)
            PrintFormat("TRAILING: activated (runningProfit=%.2f >= start=%.2f). equityPeak set to %.2f", runningProfit, InpTrailingStartUSD, equityPeak);
        }
      return;
     }

   if(equity > equityPeak)
      equityPeak = equity;
   double drop = equityPeak - equity;

   if(InpDebug)
      PrintFormat("TRAILING: active equity=%.2f peak=%.2f drop=%.2f (start=%.2f step=%.2f)", equity, equityPeak, drop, InpTrailingStartUSD, InpTrailingStepUSD);

   // equityPeak sekarang puncak P&L BASKET (bukan equity akun), jadi ambangnya
   // langsung InpTrailingStartUSD -- tidak lagi ditambah balance.
   if(equityPeak >= InpTrailingStartUSD && drop >= InpTrailingStepUSD)
     {
      PrintFormat("Global trailing triggered: peak %.2f -> now %.2f (drop %.2f >= %.2f). Closing all EA positions.",
                  equityPeak, equity, drop, InpTrailingStepUSD);
      CloseAllEAPositions();
      // after closing, reset trailingActive so it can re-activate only if profit grows again
      trailingActive = false;
      equityPeak = equity;
     }
  }


//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| P&L mengambang milik EA INI saja (magic + simbol), bukan akun.    |
//|                                                                  |
//| KENAPA INI ADA                                                   |
//| Versi sebelumnya memakai equity - balance, yaitu floating SELURUH |
//| AKUN. Saat EA ini sendirian di akun (seperti di Strategy Tester)  |
//| keduanya sama, jadi backtest tidak pernah menangkap masalahnya.   |
//| Di akun LIVE eterna jalan bersamaan, dan akibatnya tiga-tiganya   |
//| merusak:                                                          |
//|   1. eterna floating +$40 -> Global TP "tercapai" -> basket EA    |
//|      ini ditutup paksa walau sedang -$30. Rugi dikunci karena     |
//|      strategi LAIN sedang menang.                                 |
//|   2. eterna floating -$75 -> Global SL "tercapai" -> basket yang  |
//|      sedang UNTUNG ikut ditutup.                                  |
//|   3. Paling berbahaya: eterna floating +$50 sementara basket ini  |
//|      -$100 -> diff hanya -$50, di atas ambang -$75, jadi SL TIDAK |
//|      PERNAH menyala. Batas kerugian $75 gagal melindungi, dan     |
//|      kerugian bisa tumbuh tanpa batas selama profit strategi lain |
//|      menutupinya.                                                 |
//| Terlihat live 2026-08-21: "Global trailing triggered" menyala     |
//| berkali-kali saat EA ini TIDAK punya posisi sama sekali -- yang   |
//| bergerak adalah floating eterna.                                  |
//|                                                                  |
//| Memakai profit + swap supaya sebanding dengan equity - balance    |
//| (keduanya mengecualikan komisi, yang sudah masuk ke balance).     |
//+------------------------------------------------------------------+
double MyFloatingPnL(int &count)
  {
   double sum = 0.0;
   count = 0;
   for(int i = (int)PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0 || !PositionSelectByTicket(t))
         continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      sum += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      count++;
     }
   return sum;
  }

double MyFloatingPnL()
  {
   int dummy = 0;
   return MyFloatingPnL(dummy);
  }

bool CheckGlobalTP_SL()
  {
   double diff = MyFloatingPnL();
   if(InpGlobalTP_USD>0 && diff >= InpGlobalTP_USD)
     {
      PrintFormat("Global TP reached: %.2f >= %.2f. Closing all EA positions.", diff, InpGlobalTP_USD);
      CloseAllEAPositions();
      return true;
     }
   if(InpGlobalSL_USD>0 && diff <= -InpGlobalSL_USD)
     {
      PrintFormat("Global SL reached: %.2f <= -%.2f. Closing all EA positions.", diff, InpGlobalSL_USD);
      CloseAllEAPositions();
      return true;
     }
   return false;
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
void CloseAllEAPositions()
  {
   int total = (int)PositionsTotal();
   for(int ii=total-1; ii>=0; ii--)
     {
      ulong t = PositionGetTicket(ii);
      if(t==0)
         continue;
      if(!PositionSelectByTicket(t))
         continue;
      long magic = (long)PositionGetInteger(POSITION_MAGIC);
      if(magic!=InpMagicNumber)
         continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      if(sym!=_Symbol)
         continue;
      if(!trade.PositionClose(t))
         PrintFormat("Failed close %I64u : %u %s", t, trade.ResultRetcode(), trade.ResultComment());
      else
         PrintFormat("Closed %I64u by global close", t);
      int ix = FindTicketIndex(t);
      if(ix!=-1)
         RemoveTicketIndex(ix);
     }
// reset BEP trigger after manual close-all to allow new series
   layerBEPTriggered = false;
   // reset dual-entry state
   g_tp1_ticket = 0;
   g_trail_ticket = 0;
   g_trail_active = false;
   g_trail_peak_profit = 0.0;
   // cancel and reset deep entry pending limits
   if(PendingOrderExists(g_pending_buy))  { DeletePendingOrder(g_pending_buy);  }
   if(PendingOrderExists(g_pending_sell)) { DeletePendingOrder(g_pending_sell); }
   g_pending_buy  = 0;
   g_pending_sell = 0;
  }

//---------------------- Other utilities (daily/reset/count) -----------
double CalculateTodayClosedProfit()
  {
   double profit = 0.0;
   datetime today = StringToTime(TimeToString(TimeCurrent(), TIME_DATE));
   HistorySelect(today, TimeCurrent());
   int deals = HistoryDealsTotal();
   for(int i=0;i<deals;i++)
     {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket==0)
         continue;
      datetime t = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
      if(t<today)
         continue;
      profit += HistoryDealGetDouble(ticket, DEAL_PROFIT);
     }
   return profit;
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
bool CheckDailyTargetReached()
  {
   double todayProfit = CalculateTodayClosedProfit();
   dailyProfitAcc = todayProfit;
   return (InpDailyTargetUSD>0 && dailyProfitAcc >= InpDailyTargetUSD);
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
bool IsWithinTradingHours()
  {
   MqlDateTime tm;
   TimeToStruct(TimeCurrent(), tm);
   int h = tm.hour;
   if(InpStartHour <= InpEndHour)
      return (h>=InpStartHour && h<=InpEndHour);
   else
      return (h>=InpStartHour || h<=InpEndHour);
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
void ManageDailyReset()
  {
   datetime nowBar = iTime(_Symbol,_Period,0);
   if(nowBar > lastResetDaily)
     {
      MqlDateTime dt;
      TimeToStruct(TimeCurrent(), dt);
      if(dt.hour==0 && dt.min==0)
        {
         dailyProfitAcc = 0.0;
         equityPeak = AccountInfoDouble(ACCOUNT_EQUITY);
         trailingActive = false;
         if(InpDebug)
            PrintFormat("Daily reset: equityPeak reset to %.2f", equityPeak);
        }
      lastResetDaily = nowBar;
     }
  }

//---------------------- Deep Entry helpers -------------------------

// Check if a pending order (by ticket) is still open/active
bool PendingOrderExists(ulong ticket)
  {
   if(ticket == 0) return false;
   return OrderSelect(ticket);
  }

// Delete a pending order by ticket
void DeletePendingOrder(ulong ticket)
  {
   if(ticket == 0) return;
   if(!OrderSelect(ticket)) return;
   trade.OrderDelete(ticket);
   PrintFormat("DEEP ENTRY: deleted pending order ticket=%I64u", ticket);
  }

// Cancel stale pending limits if positions are all closed (new series starts)
void CleanStalePendingLimits()
  {
   int totalOpen = CountOpenPositions();
   if(totalOpen == 0)
     {
      if(PendingOrderExists(g_pending_buy))  { DeletePendingOrder(g_pending_buy);  g_pending_buy  = 0; }
      if(PendingOrderExists(g_pending_sell)) { DeletePendingOrder(g_pending_sell); g_pending_sell = 0; }
     }
  }

// Place a BuyLimit or SellLimit at the computed deep price
// Returns the ticket of the placed order (0 on failure)
ulong PlaceLimitOrder(const ENUM_ORDER_TYPE limitType, double lot, double limitPrice)
  {
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(50);

   // Expiry
   datetime expiry = 0;
   if(InpLimitExpiryMins > 0)
      expiry = TimeCurrent() + (datetime)(InpLimitExpiryMins * 60);

   double price  = NormalizeDouble(limitPrice, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
   bool   res    = false;

   if(limitType == ORDER_TYPE_BUY_LIMIT)
      res = trade.BuyLimit(lot, price, _Symbol, 0, 0, ORDER_TIME_SPECIFIED, expiry, InpOrderComment);
   else
      res = trade.SellLimit(lot, price, _Symbol, 0, 0, ORDER_TIME_SPECIFIED, expiry, InpOrderComment);

   if(!res)
     {
      PrintFormat("DEEP ENTRY ERROR: limit order failed type=%s price=%.5f lot=%.2f rc=%u %s",
                  (limitType==ORDER_TYPE_BUY_LIMIT?"BuyLimit":"SellLimit"),
                  price, lot, trade.ResultRetcode(), trade.ResultComment());
      return 0;
     }

   ulong ticket = trade.ResultOrder();
   PrintFormat("DEEP ENTRY: %s placed price=%.5f lot=%.2f ticket=%I64u expiry=%s",
               (limitType==ORDER_TYPE_BUY_LIMIT?"BuyLimit":"SellLimit"),
               price, lot, ticket, (expiry>0 ? TimeToString(expiry) : "GTC"));
   return ticket;
  }

// Compute depth multiplier for a given layer index (1-based)
double GetDepthMultiplier(int layerIndex)
  {
   switch(layerIndex)
     {
      case 1:  return InpLayer1DepthMult;
      case 2:  return InpLayer2DepthMult;
      case 3:  return InpLayer3DepthMult;
      default: return InpLayer3DepthMult * (double)layerIndex / 3.0; // extrapolate if >3
     }
  }

//-------------------------- Entry logic ----------------------------
// Open a single order and return its ticket (0 on failure)
ulong OpenOrderReturnTicket(const ENUM_ORDER_TYPE type, double lot)
  {
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(50);
   bool res = false;
   if(type == ORDER_TYPE_BUY)
      res = trade.Buy(lot, NULL, 0.0, 0.0, 0.0, InpOrderComment);
   else
      res = trade.Sell(lot, NULL, 0.0, 0.0, 0.0, InpOrderComment);

   if(!res)
     {
      uint rc = trade.ResultRetcode();
      PrintFormat("DIAG ERROR: Order failed rc=%u comment=%s GetLastError=%d", rc, trade.ResultComment(), GetLastError());
      return 0;
     }
   ulong ticket = trade.ResultOrder();
   PrintFormat("Order opened: type=%s lot=%.2f ticket=%I64u", (type == ORDER_TYPE_BUY ? "BUY" : "SELL"), lot, ticket);
   if(ticket != 0 && FindTicketIndex(ticket) == -1)
     {
      int newSize = ArraySize(trackedTickets) + 1;
      ArrayResize(trackedTickets, newSize);
      ArrayResize(trackedPeaks, newSize);
      trackedTickets[newSize - 1] = ticket;
      trackedPeaks[newSize - 1]   = 0.0;
     }
   return ticket;
  }

// Get floating profit of a single position by ticket
double GetPositionProfit(ulong ticket)
  {
   if(ticket == 0) return 0.0;
   if(!PositionSelectByTicket(ticket)) return 0.0;
   return PositionGetDouble(POSITION_PROFIT);
  }

// Check whether a position is still open
bool PositionIsOpen(ulong ticket)
  {
   if(ticket == 0) return false;
   return PositionSelectByTicket(ticket);
  }

// Close a single position by ticket
void ClosePositionByTicket(ulong ticket)
  {
   if(ticket == 0) return;
   if(!trade.PositionClose(ticket))
      PrintFormat("Failed to close ticket %I64u : %u %s", ticket, trade.ResultRetcode(), trade.ResultComment());
   else
      PrintFormat("Closed ticket %I64u", ticket);
   int ix = FindTicketIndex(ticket);
   if(ix != -1) RemoveTicketIndex(ix);
  }

// Manage per-tick logic for dual-entry positions
void ManageDualEntryPositions()
  {
   // --- Position #1: fixed TP at InpFirstEntry_TP_USD ---
   if(g_tp1_ticket != 0 && PositionIsOpen(g_tp1_ticket))
     {
      double p1 = GetPositionProfit(g_tp1_ticket);
      if(p1 >= InpFirstEntry_TP_USD)
        {
         PrintFormat("DUAL TP1: profit %.2f >= %.2f -> closing position #1", p1, InpFirstEntry_TP_USD);
         ClosePositionByTicket(g_tp1_ticket);
         g_tp1_ticket = 0;
        }
     }
   else
      g_tp1_ticket = 0; // position closed externally

   // --- Position #2: trailing at InpFirstEntry_Trail_USD ---
   if(g_trail_ticket != 0 && PositionIsOpen(g_trail_ticket))
     {
      double p2 = GetPositionProfit(g_trail_ticket);

      if(!g_trail_active)
        {
         if(p2 >= InpFirstEntry_Trail_USD)
           {
            g_trail_active = true;
            g_trail_peak_profit = p2;
            PrintFormat("DUAL TRAIL: activated at profit %.2f (target=%.2f)", p2, InpFirstEntry_Trail_USD);
           }
        }
      else
        {
         if(p2 > g_trail_peak_profit)
            g_trail_peak_profit = p2;
         // Close if profit drops by InpTrailingStepUSD from peak
         double drop = g_trail_peak_profit - p2;
         if(drop >= InpTrailingStepUSD)
           {
            PrintFormat("DUAL TRAIL: profit dropped %.2f from peak %.2f -> closing position #2", drop, g_trail_peak_profit);
            ClosePositionByTicket(g_trail_ticket);
            g_trail_ticket = 0;
            g_trail_active = false;
            g_trail_peak_profit = 0.0;
           }
        }
     }
   else
     {
      g_trail_ticket = 0;
      g_trail_active = false;
      g_trail_peak_profit = 0.0;
     }
  }

//=== REGIME GATE helpers ===========================================
// Supertrend direction on the gate timeframe, mirroring
// pipeline/live/signal.py::EternaStrategy._supertrend exactly:
//   band_up = hl2 + mult*ATR ; band_lo = hl2 - mult*ATR   (Wilder ATR)
//   final_up[i] = band_up[i] if (band_up[i] < final_up[i-1] || close[i-1] > final_up[i-1]) else final_up[i-1]
//   final_lo[i] = band_lo[i] if (band_lo[i] > final_lo[i-1] || close[i-1] < final_lo[i-1]) else final_lo[i-1]
//   dir = +1 if close[i] > final_up[i]; -1 if close[i] < final_lo[i]; else carry
// Returns +1/-1, or 0 when there is not enough data (caller treats 0 as
// "unknown" and lets the trade through rather than blocking blindly).
int SupertrendDir(const double &high[], const double &low[], const double &close[],
                  const double &atrBuf[], const int n, const double mult)
  {
   if(n < 3)
      return 0;
   double fu = 0.0, fl = 0.0;
   int dir = 1;
   bool started = false;
   for(int i = 1; i < n; i++)
     {
      double a = atrBuf[i];
      if(a <= 0.0 || !MathIsValidNumber(a))
         continue;
      double hl2 = (high[i] + low[i]) / 2.0;
      double bu  = hl2 + mult * a;
      double bl  = hl2 - mult * a;
      if(!started)
        { fu = bu; fl = bl; dir = 1; started = true; continue; }
      fu = (bu < fu || close[i-1] > fu) ? bu : fu;
      fl = (bl > fl || close[i-1] < fl) ? bl : fl;
      if(close[i] > fu)      dir = 1;
      else if(close[i] < fl) dir = -1;
     }
   return started ? dir : 0;
  }

// true  -> the two Supertrends CONFLICT (no clear trend) => fade is allowed
// true is also returned when data is unavailable, so a data hiccup cannot
// silently halt the EA; it degrades to the ungated behaviour instead.
bool RegimeAllowsNewSeries()
  {
   if(!InpUseRegimeGate)
      return true;

   // CACHE PER BAR. Fungsi ini dipanggil TIAP TICK selama EA flat, dan tiap
   // panggilan menyalin ribuan bar, membuat handle iATR, lalu menghitung DUA
   // Supertrend penuh. Di data tick M5 setahun itu jutaan kali: run 2024 tidak
   // selesai dalam 40 menit karenanya.
   //
   // Ini setara PERSIS, bukan pendekatan: seluruh input diambil dari bar
   // TERTUTUP saja (shift 1 di semua Copy*), jadi hasilnya tidak mungkin
   // berubah di tengah bar. Hanya dihitung ulang saat bar gate baru tertutup.
   static datetime s_bar = 0;
   static bool     s_val = true;
   datetime bt = iTime(_Symbol, InpGateTF, 1);
   if(bt != 0 && bt == s_bar)
      return s_val;

   int n = InpGateBars;
   double high[], low[], close[], atrBuf[];
   ArraySetAsSeries(high, false);
   ArraySetAsSeries(low, false);
   ArraySetAsSeries(close, false);
   ArraySetAsSeries(atrBuf, false);

   // shift 1 = last CLOSED bar on the gate timeframe (never the forming bar)
   if(CopyHigh(_Symbol, InpGateTF, 1, n, high)  != n) return true;
   if(CopyLow (_Symbol, InpGateTF, 1, n, low)   != n) return true;
   if(CopyClose(_Symbol, InpGateTF, 1, n, close)!= n) return true;

   int h = iATR(_Symbol, InpGateTF, InpGateATRPeriod);
   if(h == INVALID_HANDLE)
      return true;
   int got = CopyBuffer(h, 0, 1, n, atrBuf);
   IndicatorRelease(h);
   if(got != n)
      return true;

   int de = SupertrendDir(high, low, close, atrBuf, n, InpGateMultEntry);
   int dt = SupertrendDir(high, low, close, atrBuf, n, InpGateMultTrend);
   if(de == 0 || dt == 0)
      return true;                       // unknown -> do not block

   bool conflict = (de != dt);
   if(InpDebug)
      PrintFormat("GATE: ST_entry=%d ST_trend=%d -> %s", de, dt,
                  conflict ? "CONFLICT (new series allowed)" : "TREND (blocked)");
   // Simpan HANYA hasil yang benar-benar dihitung. Jalur "return true" di atas
   // adalah degradasi saat data belum siap -- kalau ikut disimpan, satu hiccup
   // akan mengunci gate terbuka untuk sisa bar itu.
   s_bar = bt;
   s_val = conflict;
   return conflict;
  }

void TryEnter(const ENUM_ORDER_TYPE orderType)
  {
   if(CheckDailyTargetReached())
      return;

   // --- NEWS FILTER: block new entries during high-impact news window ---
   if(IsNewsTime())
     {
      if(InpDebug)
         Print("TryEnter: blocked by news filter");
      return;
     }

   int totalOpen = CountOpenPositions();

   // ----------------------------------------------------------------
   // FIRST SIGNAL: no open positions -> open 2 x 0.01
   // Position #1: TP at InpFirstEntry_TP_USD ($10)
   // Position #2: trailing at InpFirstEntry_Trail_USD ($25)
   // ----------------------------------------------------------------
   if(totalOpen == 0)
     {
      // REGIME GATE: only applies to STARTING a series. An already-open basket
      // is never interrupted -- see the note at the input declarations.
      if(!RegimeAllowsNewSeries())
        {
         if(InpDebug)
            Print("TryEnter: blocked by regime gate (clear trend)");
         return;
        }

      double lot = NormalizeLot(InpStartLot); // 0.01

      // Reset dual-entry state
      g_tp1_ticket = 0;
      g_trail_ticket = 0;
      g_trail_active = false;
      g_trail_peak_profit = 0.0;

      // Open position #1 (fixed TP)
      ulong t1 = OpenOrderReturnTicket(orderType, lot);
      if(t1 != 0)
        {
         g_tp1_ticket = t1;
         PrintFormat("DUAL ENTRY: position #1 ticket=%I64u lot=%.2f TP=$%.2f", t1, lot, InpFirstEntry_TP_USD);
        }

      // Open position #2 (trailing)
      ulong t2 = OpenOrderReturnTicket(orderType, lot);
      if(t2 != 0)
        {
         g_trail_ticket = t2;
         PrintFormat("DUAL ENTRY: position #2 ticket=%I64u lot=%.2f trail_target=$%.2f", t2, lot, InpFirstEntry_Trail_USD);
        }

      return;
     }

   // ----------------------------------------------------------------
   // MARTINGALE LAYERS (layer 1, 2, 3 max) - DEEP ENTRY LIMIT ORDERS
   // Each layer places a BuyLimit/SellLimit at a deeper discount level.
   // Depth = InpOrderGapPips * DepthMultiplier[layer]
   // Only ONE pending limit per side allowed at a time.
   // ----------------------------------------------------------------
   if(!InpUseMartingale)
      return;
   if(totalOpen >= InpMaxLayers)
     {
      if(InpDebug)
         Print("DIAG: reached max layers (3)");
      return;
     }

   // If a pending limit for this side already exists and is still active, skip
   bool isBuy = (orderType == ORDER_TYPE_BUY);
   ulong existingPending = isBuy ? g_pending_buy : g_pending_sell;

   if(PendingOrderExists(existingPending))
     {
      if(InpDebug)
         PrintFormat("DEEP ENTRY: pending %s limit already exists ticket=%I64u, skipping",
                     (isBuy ? "BUY" : "SELL"), existingPending);
      return;
     }
   // Reset stale reference
   if(isBuy) g_pending_buy  = 0;
   else       g_pending_sell = 0;

   ulong lastTicket;
   double lastLot, lastPrice, lastProfit;
   datetime lastTime;
   bool found = GetLastPositionByOrderType(orderType, lastTicket, lastLot, lastPrice, lastProfit, lastTime);
   if(!found)
     {
      if(InpDebug)
         Print("DIAG: no same-side last pos");
      return;
     }
   if(lastProfit >= 0.0)
     {
      if(InpDebug)
         PrintFormat("DIAG: last profit >=0 => no martingale (%.2f)", lastProfit);
      return;
     }

   double point   = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int    digits  = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double pipMul  = (digits > 3) ? 10.0 : 1.0;
   double baseGap = InpOrderGapPips * point * pipMul; // base gap in price units

   // Determine which layer we are about to place (1-based)
   int sameCount  = CountSameSidePositions(orderType); // positions already open this side
   int nextLayer  = sameCount + 1;                     // e.g. 1 open → placing layer 2

   // Deep price = last position entry price offset by gap * depth multiplier
   double depthMult  = GetDepthMultiplier(nextLayer);
   double deepOffset = baseGap * depthMult;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double limitPrice = 0.0;
   ENUM_ORDER_TYPE limitType;

   if(orderType == ORDER_TYPE_BUY)
     {
      // BuyLimit must be BELOW current ask
      limitPrice = lastPrice - deepOffset;
      limitType  = ORDER_TYPE_BUY_LIMIT;
      // Validate: limit must be below ask by at least 1 pip
      double minDist = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
      if(limitPrice >= ask - minDist)
        {
         if(InpDebug)
            PrintFormat("DEEP ENTRY: BuyLimit price %.5f too close to ask %.5f, adjusting", limitPrice, ask);
         limitPrice = ask - minDist - point;
        }
     }
   else
     {
      // SellLimit must be ABOVE current bid
      limitPrice = lastPrice + deepOffset;
      limitType  = ORDER_TYPE_SELL_LIMIT;
      double minDist = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
      if(limitPrice <= bid + minDist)
        {
         if(InpDebug)
            PrintFormat("DEEP ENTRY: SellLimit price %.5f too close to bid %.5f, adjusting", limitPrice, bid);
         limitPrice = bid + minDist + point;
        }
     }

   // Lot sizing: 0.01 * (nextLayer + 1), hard cap 0.03
   double rawLot = InpStartLot * (double)(sameCount + 1);
   if(rawLot > 0.03) rawLot = 0.03;
   double nextLot = NormalizeLot(rawLot);

   if(InpDebug)
      PrintFormat("DEEP ENTRY: layer=%d depthMult=%.1fx baseGap=%.5f deepOffset=%.5f limitPrice=%.5f lot=%.2f",
                  nextLayer, depthMult, baseGap, deepOffset, limitPrice, nextLot);

   ulong newTicket = PlaceLimitOrder(limitType, nextLot, limitPrice);
   if(newTicket != 0)
     {
      if(isBuy) g_pending_buy  = newTicket;
      else       g_pending_sell = newTicket;
     }
  }

//---------------------- Dashboard -----------------------------
void UpdateDashboard()
  {
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double diff = equity - balance;
   string s = InpEAName + "\n";
   s += "MASource: "+IntegerToString(InpMASource)+" (0=Price,1=RSI,2=MACD_SIGNAL)\n";
   s += "ShowIndicators: "+(InpShowIndicators ? "ON":"OFF")+"\n";
   s += "Daily target (USD): "+DoubleToString(InpDailyTargetUSD,2)+"\n";
   s += "Daily closed profit (USD): "+DoubleToString(dailyProfitAcc,2)+"\n";
   s += "Equity-Balance (USD): "+DoubleToString(diff,2)+"\n";
   s += "Global TP (USD): "+DoubleToString(InpGlobalTP_USD,2)+" , SL (USD): "+DoubleToString(InpGlobalSL_USD,2)+"\n";
   s += "Open positions: "+IntegerToString(CountOpenPositions())+"\n";
   // Deep entry pending status
   string pendBuy  = PendingOrderExists(g_pending_buy)  ? ("BuyLimit #"+IntegerToString((int)g_pending_buy))  : "none";
   string pendSell = PendingOrderExists(g_pending_sell) ? ("SellLimit #"+IntegerToString((int)g_pending_sell)) : "none";
   s += "Pending Limits: BUY="+pendBuy+" | SELL="+pendSell+"\n";
   // News filter status
   if(InpUseNewsFilter)
     {
      string newsStatus = g_newsBlocked ? "🔴 BLOCKED (news window)" : "🟢 CLEAR";
      s += "News Filter: "+newsStatus+" | Impact>="+IntegerToString(InpNewsImpactLevel)
          +" | "+IntegerToString(InpNewsMinutesBefore)+"min before / "
          +IntegerToString(InpNewsMinutesAfter)+"min after\n";
     }
   s += "DUAL: TP1 ticket="+IntegerToString((int)g_tp1_ticket)+" ($"+DoubleToString(InpFirstEntry_TP_USD,0)+")"
       +" | Trail ticket="+IntegerToString((int)g_trail_ticket)
       +" active="+( g_trail_active ? "YES peak=$"+DoubleToString(g_trail_peak_profit,2) : "NO ($"+DoubleToString(InpFirstEntry_Trail_USD,0)+")")+"\n";
   s += "Last RSI-MA: "+DoubleToString(lastRsiMA,2)+"\n";
   if(InpUseTrailingUSD)
      s += "EquityPeak: "+DoubleToString(equityPeak,2)+" , TrailingStart: "+DoubleToString(InpTrailingStartUSD,2)+" , Step: "+DoubleToString(InpTrailingStepUSD,2)+"\n";
   if(InpDebug)
      s += "Tracked tickets: "+IntegerToString(ArraySize(trackedTickets))+"\n";
   Comment(s);
  }

//---------------------- News Filter (MT5 Calendar) ----------------
// Cache: last checked broker-time minute, and result
datetime g_newsLastChecked = 0;
bool     g_newsBlocked     = false;

//+------------------------------------------------------------------+
// Convert CALENDAR_EVENT_IMPORTANCE enum int to our level:
// CALENDAR_IMPORTANCE_NONE=0, LOW=1, MODERATE=2, HIGH=3
//+------------------------------------------------------------------+
bool IsNewsTime()
  {
   if(!InpUseNewsFilter)
      return false;

   datetime now = TimeCurrent(); // broker server time

   // Cache result per minute to avoid hammering the calendar API every tick
   datetime nowMinute = (datetime)(((long)now / 60) * 60);
   if(nowMinute == g_newsLastChecked)
      return g_newsBlocked;
   g_newsLastChecked = nowMinute;

   // Scan window: from (now - MinutesAfter) to (now + MinutesBefore)
   datetime scanFrom = now - (datetime)(InpNewsMinutesAfter  * 60);
   datetime scanTo   = now + (datetime)(InpNewsMinutesBefore * 60);

   MqlCalendarValue values[];
   int count = 0;

   // Try country-filtered call first; fall back to all-currency if country unknown
   if(StringLen(InpNewsCountry) > 0)
      count = CalendarValueHistory(values, scanFrom, scanTo, InpNewsCountry);
   else
      count = CalendarValueHistory(values, scanFrom, scanTo);

   if(count <= 0)
     {
      g_newsBlocked = false;
      return false;
     }

   for(int i = 0; i < count; i++)
     {
      MqlCalendarEvent ev;
      if(!CalendarEventById(values[i].event_id, ev))
         continue;

      // Map MT5 importance to integer: LOW=1, MODERATE=2, HIGH=3
      int importance = 0;
      switch(ev.importance)
        {
         case CALENDAR_IMPORTANCE_LOW:      importance = 1; break;
         case CALENDAR_IMPORTANCE_MODERATE: importance = 2; break;
         case CALENDAR_IMPORTANCE_HIGH:     importance = 3; break;
         default: importance = 0; break;
        }

      if(importance < InpNewsImpactLevel)
         continue; // below threshold, skip

      // This news event is within the window and meets impact level
      datetime evTime = values[i].time;
      if(InpDebug)
         PrintFormat("NEWS FILTER: blocking entry — event_id=%I64u importance=%d time=%s name=%s",
                     values[i].event_id, importance, TimeToString(evTime), ev.name);

      g_newsBlocked = true;
      return true;
     }

   g_newsBlocked = false;
   return false;
  }

//---------------------- Initialization                             --
int OnInit()
  {

   ArrayResize(trackedTickets,0);
   ArrayResize(trackedPeaks,0);
   chart_id_global = ChartID();
   AutoAddBuiltInRSI();
   lastResetDaily = iTime(_Symbol,_Period,0);
   dailyProfitAcc = 0.0;
   equityPeak = AccountInfoDouble(ACCOUNT_EQUITY);
   trailingActive = false;
   g_tp1_ticket = 0;
   g_trail_ticket = 0;
   g_trail_active = false;
   g_trail_peak_profit = 0.0;
   g_pending_buy  = 0;
   g_pending_sell = 0;
   g_newsLastChecked = 0;
   g_newsBlocked = false;
   EventSetTimer(1);
   trade.SetExpertMagicNumber(InpMagicNumber);
   PrintFormat("%s initialized (v1.70 full). ShowIndicators=%s DrawBars=%d", InpEAName, (InpShowIndicators?"ON":"OFF"), InpDrawBars);
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   if(InpShowIndicators)
     {
      DeleteObjectsWithPrefix(OBJ_PREF_RSI);
      DeleteObjectsWithPrefix(OBJ_PREF_SMA);
      DeleteObjectsWithPrefix(OBJ_PREF_MACD);
      ObjectDelete(chart_id_global, OBJ_PREF_LVL + "80");
      ObjectDelete(chart_id_global, OBJ_PREF_LVL + "50");
      ObjectDelete(chart_id_global, OBJ_PREF_LVL + "15");
      AutoRemoveBuiltInRSI();
     }
   EventKillTimer();
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
void OnTick()
  {
   ManageDailyReset();
   CleanStalePendingLimits(); // cancel orphan pending limits if all positions closed

   if(CheckGlobalTP_SL())
     {
      UpdateDashboard();
      return;
     }
   if(CheckDailyTargetReached())
     {
      UpdateDashboard();
      return;
     }
   if(!IsWithinTradingHours())
     {
      UpdateDashboard();
      return;
     }

   int barsAvail = Bars(_Symbol, _Period);
   if(barsAvail < RequiredHistory())
     {
      if(InpDebug)
         PrintFormat("DIAG: not enough bars %d/%d", barsAvail, RequiredHistory());
      UpdateDashboard();
      return;
     }

   int drawBars = MathMin(InpDrawBars, MathMax(10, barsAvail-1));

// compute RSI first
   double rsiSeries[];
   ArrayResize(rsiSeries, drawBars);
   ArraySetAsSeries(rsiSeries, true);
   double rsiMaSeries[];
   ArrayResize(rsiMaSeries, drawBars);
   ArraySetAsSeries(rsiMaSeries, true);
     {
      int need = InpRSIPeriod + InpMAPeriod + drawBars + 10;
      double closes[];
      ArrayResize(closes, need);
      ArraySetAsSeries(closes, true);
      int copied = CopyClose(_Symbol, _Period, 1, need, closes);
      if(copied>0)
        {
         ArrayResize(closes, copied);
         for(int b=0;b<drawBars;b++)
           {
            if(b + InpRSIPeriod >= copied)
              {
               rsiSeries[b] = 50.0;
               continue;
              }
            double gain=0.0, loss=0.0;
            for(int j=1;j<=InpRSIPeriod;j++)
              {
               double change = closes[j-1 + b] - closes[j + b];
               if(change>0)
                  gain += change;
               else
                  loss += -change;
              }
            double avgG = gain / InpRSIPeriod;
            double avgL = loss / InpRSIPeriod;
            double rsi;
            if(avgL==0.0 && avgG==0.0)
               rsi = 50.0;
            else
               if(avgL==0.0)
                  rsi = 100.0;
               else
                 {
                  double rs = avgG/avgL;
                  rsi = 100.0 - (100.0/(1.0 + rs));
                 }
            rsiSeries[b] = rsi;
           }
         for(int b=0;b<drawBars;b++)
           {
            if(InpMAPeriod<=1)
               rsiMaSeries[b] = rsiSeries[b];
            else
              {
               double sum=0.0;
               int cnt=0;
               for(int m=0;m<InpMAPeriod;m++)
                 {
                  int pos = b + m;
                  if(pos >= ArraySize(rsiSeries))
                     break;
                  sum += rsiSeries[pos];
                  cnt++;
                 }
               rsiMaSeries[b] = (cnt>0) ? sum/cnt : rsiSeries[b];
              }
           }
        }
     }
   lastRsiMA = rsiMaSeries[0];

// compute MACD & SMA; call Compute with rsiSeries if needed, otherwise pass empty
   double macdSignalNorm[], smaNorm[];
   ArrayResize(macdSignalNorm, drawBars);
   ArrayResize(smaNorm, drawBars);

   double rsiEmpty[];
   ArrayResize(rsiEmpty,0);
   bool ok=false;
   if(InpMASource == 1)
     {
      int rsiLen = ArraySize(rsiSeries);
      ok = Compute_MACD_and_SMA_Normalized(InpMACDFast, InpMACDSlow, InpMACDSignal, InpMASmaPeriod, InpMACDNormalizePeriod, drawBars, macdSignalNorm, smaNorm, rsiSeries, rsiLen);
     }
   else
     {
      ok = Compute_MACD_and_SMA_Normalized(InpMACDFast, InpMACDSlow, InpMACDSignal, InpMASmaPeriod, InpMACDNormalizePeriod, drawBars, macdSignalNorm, smaNorm, rsiEmpty, 0);
     }

   if(!ok)
     {
      if(InpDebug)
         Print("DIAG: MACD/SMA not ready");
      UpdateDashboard();
      return;
     }

   if(InpShowIndicators)
     {
      DrawLevelLine(OBJ_PREF_LVL + "80", InpRSILevelHigh, clrRed);
      DrawLevelLine(OBJ_PREF_LVL + "50", InpRSILevelMid, clrGray);
      DrawLevelLine(OBJ_PREF_LVL + "15", InpRSILevelLow, clrLime);

      DrawSeriesAsTrends(rsiSeries, drawBars, OBJ_PREF_RSI, InpColorRSI);
      DrawSeriesAsTrends(smaNorm, drawBars, OBJ_PREF_SMA, InpColorSMA);
      DrawSeriesAsTrends(macdSignalNorm, drawBars, OBJ_PREF_MACD, InpColorMACDNorm);
     }

   if(InpDebug)
      PrintFormat("DEBUG: MACD_signal_norm=%.2f  SMA_norm=%.2f  RSI(0)=%.2f", macdSignalNorm[0], smaNorm[0], rsiSeries[0]);

// --- Conservative confirmation ---
   bool macdSell = (macdSignalNorm[0] >= InpRSILevelHigh);
   bool smaSell  = (smaNorm[0]       >= InpRSILevelHigh);
   bool macdBuy  = (macdSignalNorm[0] <= InpRSILevelLow);
   bool smaBuy   = (smaNorm[0]       <= InpRSILevelLow);

   bool rawSell = false;
   bool rawBuy  = false;

   switch(InpSignalMode)
     {
      case 0: // AND - require both MACD & SMA
         rawSell = macdSell && smaSell;
         rawBuy  = macdBuy  && smaBuy;
         break;
      case 1: // OR - either indicator is enough
         rawSell = macdSell || smaSell;
         rawBuy  = macdBuy  || smaBuy;
         break;
      case 2: // SMA only
         rawSell = smaSell;
         rawBuy  = smaBuy;
         break;
      case 3: // MACD only
         rawSell = macdSell;
         rawBuy  = macdBuy;
         break;
      default:
         // fallback to AND
         rawSell = macdSell && smaSell;
         rawBuy  = macdBuy  && smaBuy;
     }

   bool sellSignal = false, buySignal = false;

   if(!InpRequireBreakConfirm)
     {
      // original immediate behavior
      sellSignal = rawSell;
      buySignal  = rawBuy;
     }
   else
     {
      // SELL side confirmation: require initial breakout -> pullback below level -> re-break
      if(rawSell)
        {
         if(!waitSellConfirm)
           {
            waitSellConfirm = true;
            sellPulled = false;
            waitSellTicks = 0;
            if(InpDebug)
               Print("CONFIRM: initial SELL breakout detected, waiting for pullback and re-break");
           }
         else
           {
            if(sellPulled)
              {
               // re-break confirmed
               sellSignal = true;
               waitSellConfirm = false;
               sellPulled = false;
               waitSellTicks = 0;
               if(InpDebug)
                  Print("CONFIRM: SELL re-break confirmed -> attempting entry");
              }
           }
        }
      else
        {
         if(waitSellConfirm && !sellPulled)
           {
            // detect pullback below the high level
            if(macdSignalNorm[0] < InpRSILevelHigh && smaNorm[0] < InpRSILevelHigh)
              {
               sellPulled = true;
               waitSellTicks = 0;
               if(InpDebug)
                  Print("CONFIRM: SELL pulled back below level, waiting for re-break");
              }
            else
              {
               if(InpConfirmMaxTicks>0)
                 {
                  waitSellTicks++;
                  if(waitSellTicks > InpConfirmMaxTicks)
                    {
                     waitSellConfirm = false;
                     sellPulled = false;
                     waitSellTicks = 0;
                     if(InpDebug)
                        Print("CONFIRM: SELL wait timed out, reset");
                    }
                 }
              }
           }
        }

      if(rawBuy && waitSellConfirm)
        {
         waitSellConfirm = false;
         sellPulled = false;
         waitSellTicks = 0;
         if(InpDebug)
            Print("CONFIRM: SELL wait reset by BUY breakout");
        }

      // BUY side confirmation (mirror of SELL logic)
      if(rawBuy)
        {
         if(!waitBuyConfirm)
           {
            waitBuyConfirm = true;
            buyPulled = false;
            waitBuyTicks = 0;
            if(InpDebug)
               Print("CONFIRM: initial BUY breakout detected, waiting for pullback and re-break");
           }
         else
           {
            if(buyPulled)
              {
               buySignal = true;
               waitBuyConfirm = false;
               buyPulled = false;
               waitBuyTicks = 0;
               if(InpDebug)
                  Print("CONFIRM: BUY re-break confirmed -> attempting entry");
              }
           }
        }
      else
        {
         if(waitBuyConfirm && !buyPulled)
           {
            // detect pullback above the low level
            if(macdSignalNorm[0] > InpRSILevelLow && smaNorm[0] > InpRSILevelLow)
              {
               buyPulled = true;
               waitBuyTicks = 0;
               if(InpDebug)
                  Print("CONFIRM: BUY pulled back above low level, waiting for re-break");
              }
            else
              {
               if(InpConfirmMaxTicks>0)
                 {
                  waitBuyTicks++;
                  if(waitBuyTicks > InpConfirmMaxTicks)
                    {
                     waitBuyConfirm = false;
                     buyPulled = false;
                     waitBuyTicks = 0;
                     if(InpDebug)
                        Print("CONFIRM: BUY wait timed out, reset");
                    }
                 }
              }
           }
        }

      if(rawSell && waitBuyConfirm)
        {
         waitBuyConfirm = false;
         buyPulled = false;
         waitBuyTicks = 0;
         if(InpDebug)
            Print("CONFIRM: BUY wait reset by SELL breakout");
        }
     }

   if(InpLayerOncePerSeries && layerBEPTriggered)
     {
      // do not reset layer trigger here, it will be reset after CloseAllEAPositions()
     }

   ManageGlobalTrailing();

// NEW v8: manage dual-entry positions (TP1 and trailing for position #2)
   ManageDualEntryPositions();

// NEW: monitor layer-N BEP condition and close-all if target hit
   MonitorLayerBEP();

   if(sellSignal)
      TryEnter(ORDER_TYPE_SELL);
   else
      if(buySignal)
         TryEnter(ORDER_TYPE_BUY);

   UpdateDashboard();
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
void OnTimer()
  {
   if(CheckGlobalTP_SL())
     {
      UpdateDashboard();
      return;
     }
   ManageGlobalTrailing();
   ManageDualEntryPositions();
   MonitorLayerBEP();
   UpdateDashboard();
  }

//---------------------- Layer-N BEP logic ----------------
int CountSameSidePositions(const ENUM_ORDER_TYPE orderType)
  {
   int cnt=0;
   int total = (int)PositionsTotal();
   for(int i=0;i<total;i++)
     {
      ulong t = PositionGetTicket(i);
      if(t==0)
         continue;
      if(!PositionSelectByTicket(t))
         continue;
      long magic = (long)PositionGetInteger(POSITION_MAGIC);
      if(magic!=InpMagicNumber)
         continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      if(sym!=_Symbol)
         continue;
      long ptype = (long)PositionGetInteger(POSITION_TYPE);
      long want = (orderType==ORDER_TYPE_BUY) ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
      if(ptype == want)
         cnt++;
     }
   return cnt;
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
bool ComputeSameSideBEP(const ENUM_ORDER_TYPE orderType, double &out_bep)
  {
   double volSum = 0.0;
   double weighted = 0.0;
   int total = (int)PositionsTotal();
   for(int i=0;i<total;i++)
     {
      ulong t = PositionGetTicket(i);
      if(t==0)
         continue;
      if(!PositionSelectByTicket(t))
         continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      long ptype = (long)PositionGetInteger(POSITION_TYPE);
      long want = (orderType==ORDER_TYPE_BUY) ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
      if(ptype != want)
         continue;
      double vol = PositionGetDouble(POSITION_VOLUME);
      double price = PositionGetDouble(POSITION_PRICE_OPEN);
      volSum += vol;
      weighted += price * vol;
     }
   if(volSum <= 0.0)
      return false;
   out_bep = weighted / volSum;
   return true;
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
void MonitorLayerBEP()
  {
   if(InpLayerOncePerSeries && layerBEPTriggered)
      return;

   ENUM_ORDER_TYPE sides[2];
   sides[0] = ORDER_TYPE_BUY;
   sides[1] = ORDER_TYPE_SELL;
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double pipMul = (digits>3) ? 10.0 : 1.0;

   for(int s=0;s<2;s++)
     {
      ENUM_ORDER_TYPE side = sides[s];
      int cnt = CountSameSidePositions(side);
      if(cnt < InpLayerStartBEP)
         continue;

      double bep;
      if(!ComputeSameSideBEP(side, bep))
         continue;

      double offset = InpLayerBEPOffsetPips * point * pipMul;
      double targetPrice = (side==ORDER_TYPE_BUY) ? (bep + offset) : (bep - offset);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

      if(InpDebug)
         PrintFormat("BEP DEBUG: side=%s count=%d BEP=%.5f offset_pips=%.2f target=%.5f bid=%.5f ask=%.5f",
                     (side==ORDER_TYPE_BUY? "BUY" : "SELL"), cnt, bep, InpLayerBEPOffsetPips, targetPrice, bid, ask);

      if(side==ORDER_TYPE_BUY && bid >= targetPrice)
        {
         PrintFormat("LAYER: BUY side reached BEP+%.1fpips (%.5f >= %.5f) -> closing all EA positions.", InpLayerBEPOffsetPips, bid, targetPrice);
         CloseAllEAPositions();
         layerBEPTriggered = true;
         return;
        }
      if(side==ORDER_TYPE_SELL && ask <= targetPrice)
        {
         PrintFormat("LAYER: SELL side reached BEP-%.1fpips (%.5f <= %.5f) -> closing all EA positions.", InpLayerBEPOffsetPips, ask, targetPrice);
         CloseAllEAPositions();
         layerBEPTriggered = true;
         return;
        }
     }
  }

//+------------------------------------------------------------------+
