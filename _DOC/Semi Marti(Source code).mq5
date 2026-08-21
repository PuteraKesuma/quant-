//+------------------------------------------------------------------+
//| EA: Semi_Martingale_full.mq5                                     |
//| Version: 7.0 - Full release                                      |
//| confirmation logic (pullback + re-break).                        |
//+------------------------------------------------------------------+
#property copyright "Voyager Labs"
#property version   "7.00"
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
input double InpRSILevelLow      = 15.0;

input int    InpMACDFast         = 5;
input int    InpMACDSlow         = 13;
input int    InpMACDSignal       = 9;
input int    InpMACDNormalizePeriod = 100;

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
input int    InpMASource         = 0;    // 0=PRICE,1=RSI,2=MACD_SIGNAL()
input int    InpMASmaPeriod      = 1;

input group "Trading Parameters";
input bool   InpUseMartingale    = true;
input double InpStartLot         = 0.01;
input double InpLotMultiplier    = 1.5;
input int    InpMaxLayers        = 15;
input double InpOrderGapPips     = 200.0;
input int    InpLayerStartBEP    = 5; // layer number when BEP scan starts
input double InpLayerBEPOffsetPips = 1.0; // offset in pips from BEP to trigger
input bool   InpLayerOncePerSeries = true; // trigger BEP only once per martingale series

input double InpGlobalTP_USD     = 10.0;
input double InpGlobalSL_USD     = 0;
input double InpDailyTargetUSD   = 50.0;

input bool   InpUseTrailingUSD   = true;
input double InpTrailingStartUSD = 10.0;
input double InpTrailingStepUSD  = 5.0;

input group "Time Filter";
input int    InpStartHour        = 0;
input int    InpEndHour          = 23;

input group "Confirmation";
input bool   InpRequireBreakConfirm = true; // require pullback + re-break confirmation before opening
// max OnTick cycles to wait for confirmation before reset (0 = wait indefinitely, no timeout)
input int    InpConfirmMaxTicks = 0;

input group "UI";
input bool   InpShowIndicators   = true;
input int    InpDrawBars         = 100;
input color  InpColorRSI         = clrDodgerBlue;
input color  InpColorSMA         = clrOrange;
input color  InpColorMACDNorm    = clrMediumVioletRed;

string InpEAName           = "Minimal_Martingale";
string InpOrderComment     = "TG: @smaugh6";
input int    InpMagicNumber      = 20250822;
input bool   InpDebug            = false;

// New: signal mode - 0=AND,1=OR,2=SMA only,3=MACD only
input int    InpSignalMode       = 0;

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

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double runningProfit = equity - balance;


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

   if(equityPeak >= (balance + InpTrailingStartUSD) && drop >= InpTrailingStepUSD)
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
bool CheckGlobalTP_SL()
  {
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double diff = equity - balance;
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

//-------------------------- Entry logic ----------------------------
void TryEnter(const ENUM_ORDER_TYPE orderType)
  {
   if(CheckDailyTargetReached())
      return;
   int totalOpen = CountOpenPositions();

   if(totalOpen==0)
     {
      double lot = NormalizeLot(InpStartLot);
      OpenOrder(orderType, lot);
      return;
     }

   if(!InpUseMartingale)
      return;
   if(totalOpen >= InpMaxLayers)
     {
      if(InpDebug)
         Print("DIAG: reached max layers");
      return;
     }

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

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double pipMul = (digits>3) ? 10.0 : 1.0;
   double gapPrice = InpOrderGapPips * point * pipMul;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   if(InpOrderGapPips > 0.0)
     {
      bool gapok = false;
      if(orderType == ORDER_TYPE_BUY)
         gapok = (lastPrice - ask) >= gapPrice;
      else
         gapok = (bid - lastPrice) >= gapPrice;
      if(!gapok)
        {
         if(InpDebug)
            PrintFormat("DIAG: gap not reached lastPrice=%.5f ask=%.5f bid=%.5f gap=%.5f", lastPrice, ask, bid, gapPrice);
         return;
        }
     }

   double nextRaw = lastLot * InpLotMultiplier;
   double nextLot = NormalizeLot(nextRaw);
   if(nextLot <= lastLot)
     {
      double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      if(step <= 0.0)
         step = 0.01;
      nextLot = lastLot + step;
      nextLot = NormalizeLot(nextLot);
      if(nextLot <= lastLot)
         nextLot = lastLot + step;
     }
   if(nextLot <= 0.0 || nextLot > MaxLotAllowed())
     {
      if(InpDebug)
         PrintFormat("DIAG: invalid nextLot=%.2f", nextLot);
      return;
     }
   OpenOrder(orderType, nextLot);
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
   s += "Last RSI-MA: "+DoubleToString(lastRsiMA,2)+"\n";
   if(InpUseTrailingUSD)
      s += "EquityPeak: "+DoubleToString(equityPeak,2)+" , TrailingStart: "+DoubleToString(InpTrailingStartUSD,2)+" , Step: "+DoubleToString(InpTrailingStepUSD,2)+"\n";
   if(InpDebug)
      s += "Tracked tickets: "+IntegerToString(ArraySize(trackedTickets))+"\n";
   Comment(s);
  }

//---------------------- OnInit / OnDeinit / OnTick ----------------
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
