//+------------------------------------------------------------------+
//|                                            SignalExecutor.mq5     |
//|   Strategy-agnostic execution layer for the research project.     |
//|   Polls the local FastAPI signal server every second and brings   |
//|   each strategy "slot" into its desired state. The EA knows        |
//|   nothing about ORB or any model — it just executes the list the   |
//|   server returns. Multiple slots (multi-model) run concurrently,   |
//|   each as its own position tagged by `magic`.                      |
//|                                                                    |
//|   Idempotent via per-slot signal_id: 1s polling never duplicates   |
//|   orders; the broker's SL/TP closes each trade.                    |
//+------------------------------------------------------------------+
#property copyright "ORB Research"
#property version   "2.10"
#property strict

#include <Trade/Trade.mqh>

//--- inputs
input string ServerURL       = "http://127.0.0.1:8000"; // signal server (must be whitelisted)
input string ServerSymbol    = "NAS100";                // research key sent to the server (/signals?symbol=)
input string TradeSymbol     = "US100";                 // BROKER symbol used to place orders (FBS: US100)
input int    PollSeconds     = 1;                        // polling cadence
input int    Slippage        = 20;                       // max deviation in points
input double MaxLot          = 1.0;                     // safety clamp per order
input int    HttpTimeout     = 800;                     // WebRequest timeout (ms)
input bool   ShowPanel       = true;                     // on-chart heartbeat panel
input int    HeartbeatSeconds = 15;                      // Experts-log heartbeat cadence (0=off)
input double BreakevenAtR     = 1.0;                     // move SL to entry once profit >= this x risk (0=off)
input string BreakevenMagics  = "920621";               // CSV of magics BE applies to (empty=all). Keep ORB out!

//--- state
CTrade   trade;
long     g_magics[];      // per-slot: magic ...
string   g_last_ids[];    // ... and the last signal_id already acted on

//--- heartbeat status (for the panel + log)
bool     g_connected = false;
datetime g_last_ok   = 0;       // local time of last successful poll
int      g_last_http = 0;
int      g_last_err  = 0;
int      g_slots     = 0;
string   g_summary   = "-";     // e.g. "orb30_nas:FLAT"

//+------------------------------------------------------------------+
int OnInit()
  {
   trade.SetDeviationInPoints(Slippage);

   char   post[], res[];
   string hdr;
   int    code = WebRequest("GET", ServerURL + "/health", NULL, HttpTimeout, post, res, hdr);
   if(code == -1)
     {
      PrintFormat("WebRequest failed (err=%d). Add %s to Tools>Options>Expert Advisors>WebRequest allowed URLs.",
                  GetLastError(), ServerURL);
      return(INIT_FAILED);
     }
   PrintFormat("Health check HTTP %d: %s", code, CharArrayToString(res, 0, WHOLE_ARRAY, CP_UTF8));

   EventSetTimer(MathMax(1, PollSeconds));
   Print("SignalExecutor started, polling ", ServerURL, "/signals?symbol=", ServerSymbol,
         " -> trading ", TradeSymbol);
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason) { EventKillTimer(); Comment(""); }

//+------------------------------------------------------------------+
void OnTimer()
  {
   bool can_trade = TerminalInfoInteger(TERMINAL_CONNECTED) && MQLInfoInteger(MQL_TRADE_ALLOWED);

   string json;
   if(FetchSignals(json))               // localhost works even if broker disconnected
     {
      g_connected = true;
      g_last_ok   = TimeLocal();
      string objs[];
      int n = ExtractObjects(json, objs);   // one object per strategy slot
      string summary = "";
      for(int i = 0; i < n; i++)
        {
         summary += JsonGetStr(objs[i], "strategy") + ":" + JsonGetStr(objs[i], "action") + "  ";
         if(can_trade) HandleSlot(objs[i]);  // only execute when trading is allowed
        }
      g_slots   = n;
      g_summary = (n > 0 ? summary : "-");
     }
   else
      g_connected = false;

   if(can_trade) ManageBreakeven();     // generic risk-mgmt: SL->entry at +R (selected magics)

   UpdatePanel(can_trade);
   HeartbeatLog(can_trade);
  }

//+------------------------------------------------------------------+
//| Breakeven: once a position is +BreakevenAtR in profit, move its  |
//| SL to entry. Generic risk management (no strategy knowledge); it |
//| only touches magics in BreakevenMagics so ORB stays as backtested.|
//+------------------------------------------------------------------+
bool BeAppliesTo(long magic)
  {
   if(StringLen(BreakevenMagics) == 0) return true;        // empty = all managed
   string parts[];
   int k = StringSplit(BreakevenMagics, ',', parts);
   for(int i = 0; i < k; i++)
     {
      string p = parts[i];
      StringTrimLeft(p);
      StringTrimRight(p);
      if(StringToInteger(p) == magic) return true;
     }
   return false;
  }

void ManageBreakeven()
  {
   if(BreakevenAtR <= 0.0) return;
   int digits = (int)SymbolInfoInteger(TradeSymbol, SYMBOL_DIGITS);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != TradeSymbol) continue;
      long magic = PositionGetInteger(POSITION_MAGIC);
      if(!BeAppliesTo(magic)) continue;

      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl   = PositionGetDouble(POSITION_SL);
      if(sl <= 0.0) continue;                               // need original SL to size 1R
      long   type = PositionGetInteger(POSITION_TYPE);
      double risk = MathAbs(open - sl);
      if(risk <= 0.0) continue;

      double cur    = (type == POSITION_TYPE_BUY) ? SymbolInfoDouble(TradeSymbol, SYMBOL_BID)
                                                  : SymbolInfoDouble(TradeSymbol, SYMBOL_ASK);
      double profit = (type == POSITION_TYPE_BUY) ? (cur - open) : (open - cur);
      if(profit < BreakevenAtR * risk) continue;            // not +R yet

      if(type == POSITION_TYPE_BUY  && sl >= open) continue; // already at/above BE
      if(type == POSITION_TYPE_SELL && sl <= open) continue;

      double newsl = NormalizeDouble(open, digits);
      double tp    = PositionGetDouble(POSITION_TP);
      if(trade.PositionModify(ticket, newsl, tp))
         PrintFormat("[BE] magic %I64d SL->entry %.*f (profit %.*f >= %.2fR)",
                     magic, digits, newsl, digits, profit, BreakevenAtR);
     }
  }

//+------------------------------------------------------------------+
//| On-chart heartbeat panel                                         |
//+------------------------------------------------------------------+
void UpdatePanel(bool can_trade)
  {
   if(!ShowPanel) return;
   string s = "=== SignalExecutor ===\n";
   s += "Server : " + ServerURL + "   [" + (g_connected ? "UP" : "DOWN") + "]\n";
   if(g_connected)
      s += "Updated: " + TimeToString(g_last_ok, TIME_SECONDS)
         + "  (" + IntegerToString((int)(TimeLocal() - g_last_ok)) + "s ago)\n";
   else
      s += "Last err: HTTP=" + IntegerToString(g_last_http) + " err=" + IntegerToString(g_last_err)
         + "  (server down? start run_server)\n";
   s += "Query  : " + ServerSymbol + "  ->  trading " + TradeSymbol + "\n";
   s += "Trading: " + (can_trade ? "ENABLED" : "DISABLED (turn on Algo Trading)") + "\n";
   s += "Signals: " + g_summary + "\n";
   s += "MyPos  : " + IntegerToString(CountMyPositions()) + " on " + TradeSymbol + "\n";
   Comment(s);
  }

//+------------------------------------------------------------------+
//| Throttled heartbeat line to the Experts log                      |
//+------------------------------------------------------------------+
void HeartbeatLog(bool can_trade)
  {
   if(HeartbeatSeconds <= 0) return;
   static datetime last = 0;
   if(TimeLocal() - last < HeartbeatSeconds) return;
   last = TimeLocal();
   PrintFormat("HEARTBEAT | server=%s | trading=%s | %s | mypos=%d",
               (g_connected ? "UP" : "DOWN"), (can_trade ? "ON" : "OFF"),
               g_summary, CountMyPositions());
  }

//+------------------------------------------------------------------+
//| Count positions this EA manages on TradeSymbol                   |
//+------------------------------------------------------------------+
int CountMyPositions()
  {
   int c = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(!PositionSelectByTicket(tk)) continue;
      if(PositionGetString(POSITION_SYMBOL) == TradeSymbol) c++;
     }
   return(c);
  }

//+------------------------------------------------------------------+
//| Process one slot object: reconcile its position if signal is new |
//+------------------------------------------------------------------+
void HandleSlot(string obj)
  {
   string action    = JsonGetStr(obj, "action");
   string signal_id = JsonGetStr(obj, "signal_id");
   string strategy  = JsonGetStr(obj, "strategy");
   long   magic     = (long)JsonGetNum(obj, "magic");
   if(signal_id == "" || action == "" || magic == 0)
     {
      Print("Bad slot payload: ", obj);
      return;
     }

   if(GetLastId(magic) == signal_id)     // already acted on this slot's signal
      return;

   double sl  = JsonGetNum(obj, "sl");
   double tp  = JsonGetNum(obj, "tp");
   double lot = JsonGetNum(obj, "lot");

   if(ReconcileTo(magic, strategy, action, sl, tp, lot))
      SetLastId(magic, signal_id);       // commit only on success, else retry next poll
  }

//+------------------------------------------------------------------+
//| HTTP GET /signals -> raw JSON body                               |
//+------------------------------------------------------------------+
bool FetchSignals(string &out)
  {
   char   post[], res[];
   string hdr;
   string url = ServerURL + "/signals?symbol=" + ServerSymbol;
   int    code = WebRequest("GET", url, NULL, HttpTimeout, post, res, hdr);
   if(code != 200)
     {
      g_last_http = code;
      g_last_err  = GetLastError();
      static datetime last_warn = 0;
      if(TimeLocal() - last_warn > 30)
        {
         PrintFormat("Signal request failed (HTTP=%d, err=%d)", g_last_http, g_last_err);
         last_warn = TimeLocal();
        }
      return(false);
     }
   out = CharArrayToString(res, 0, WHOLE_ARRAY, CP_UTF8);
   return(true);
  }

//+------------------------------------------------------------------+
//| Bring one slot's position (identified by magic) into the state   |
//+------------------------------------------------------------------+
bool ReconcileTo(long magic, string strategy, string action, double sl, double tp, double lot)
  {
   ulong ticket;
   int   cur = PositionDir(TradeSymbol, magic, ticket);   // +1 long, -1 short, 0 none

   if(action == "FLAT")
      return(cur == 0 ? true : ClosePos(ticket));

   if(action == "BUY")
     {
      if(cur == 1) return(true);
      if(cur == -1 && !ClosePos(ticket)) return(false);
      return(OpenPos(true, magic, strategy, sl, tp, lot));
     }

   if(action == "SELL")
     {
      if(cur == -1) return(true);
      if(cur == 1 && !ClosePos(ticket)) return(false);
      return(OpenPos(false, magic, strategy, sl, tp, lot));
     }

   Print("Unknown action: ", action);
   return(false);
  }

//+------------------------------------------------------------------+
//| Position for symbol+magic: returns dir and fills ticket          |
//+------------------------------------------------------------------+
int PositionDir(string sym, long magic, ulong &ticket)
  {
   ticket = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(!PositionSelectByTicket(tk)) continue;
      if(PositionGetString(POSITION_SYMBOL) != sym) continue;
      if(PositionGetInteger(POSITION_MAGIC) != magic) continue;
      ticket = tk;
      return(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1);
     }
   return(0);
  }

//+------------------------------------------------------------------+
bool ClosePos(ulong ticket)
  {
   bool ok = trade.PositionClose(ticket);
   if(!ok) PrintFormat("Close failed: %d / %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
   return(ok);
  }

//+------------------------------------------------------------------+
bool OpenPos(bool is_buy, long magic, string strategy, double sl, double tp, double lot)
  {
   double vol = NormalizeLot(lot);
   if(vol <= 0) { Print("Lot resolves to 0, skip."); return(false); }

   int    digits = (int)SymbolInfoInteger(TradeSymbol, SYMBOL_DIGITS);
   double nsl = (sl > 0 ? NormalizeDouble(sl, digits) : 0.0);
   double ntp = (tp > 0 ? NormalizeDouble(tp, digits) : 0.0);

   // Defensive stops check: never fire an order whose SL/TP is already on the wrong
   // side of the market (e.g. price whipsawed past the SL). Such orders are rejected
   // as "invalid stops" (10016) and, without this guard, retried every poll. Return
   // true so the caller marks the signal handled -> no per-second spam. The server's
   // SL/TP-hit logic normally sends FLAT first; this is the second line of defence.
   double bid = SymbolInfoDouble(TradeSymbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(TradeSymbol, SYMBOL_ASK);
   double mind = SymbolInfoInteger(TradeSymbol, SYMBOL_TRADE_STOPS_LEVEL)
                 * SymbolInfoDouble(TradeSymbol, SYMBOL_POINT);
   bool bad = is_buy
              ? ((nsl > 0 && nsl >= bid - mind) || (ntp > 0 && ntp <= ask + mind))
              : ((nsl > 0 && nsl <= ask + mind) || (ntp > 0 && ntp >= bid - mind));
   if(bad)
     {
      PrintFormat("[%s] %s skipped: stops invalid vs market (bid=%.*f ask=%.*f sl=%.*f tp=%.*f) - signal stale",
                  strategy, (is_buy ? "BUY" : "SELL"), digits, bid, digits, ask, digits, nsl, digits, ntp);
      return(true);                                   // handled: do not retry this signal
     }

   trade.SetExpertMagicNumber(magic);                 // tag this slot's position
   bool ok = is_buy ? trade.Buy(vol, TradeSymbol, 0.0, nsl, ntp, strategy)
                    : trade.Sell(vol, TradeSymbol, 0.0, nsl, ntp, strategy);
   if(!ok)
      PrintFormat("[%s] %s failed: %d / %s", strategy, (is_buy ? "Buy" : "Sell"),
                  trade.ResultRetcode(), trade.ResultRetcodeDescription());
   else
      PrintFormat("[%s] %s %.2f %s sl=%.5f tp=%.5f (magic=%I64d)",
                  strategy, (is_buy ? "BUY" : "SELL"), vol, TradeSymbol, nsl, ntp, magic);
   return(ok);
  }

//+------------------------------------------------------------------+
double NormalizeLot(double lot)
  {
   double mn   = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_MIN);
   double mx   = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_STEP);
   lot = MathMin(lot, MaxLot);
   lot = MathMax(mn, MathMin(lot, mx));
   if(step > 0) lot = MathRound(lot / step) * step;
   return(lot);
  }

//+------------------------------------------------------------------+
//| Per-slot signal_id memory (parallel arrays keyed by magic)       |
//+------------------------------------------------------------------+
string GetLastId(long magic)
  {
   for(int i = 0; i < ArraySize(g_magics); i++)
      if(g_magics[i] == magic) return(g_last_ids[i]);
   return("");
  }

void SetLastId(long magic, string id)
  {
   for(int i = 0; i < ArraySize(g_magics); i++)
      if(g_magics[i] == magic) { g_last_ids[i] = id; return; }
   int n = ArraySize(g_magics);
   ArrayResize(g_magics, n + 1);
   ArrayResize(g_last_ids, n + 1);
   g_magics[n] = magic;
   g_last_ids[n] = id;
  }

//+------------------------------------------------------------------+
//| Split the "signals":[ {..},{..} ] array into object substrings   |
//+------------------------------------------------------------------+
int ExtractObjects(string json, string &objs[])
  {
   ArrayResize(objs, 0);
   int p = StringFind(json, "\"signals\"");
   if(p < 0) return(0);
   p = StringFind(json, "[", p);
   if(p < 0) return(0);

   int n = StringLen(json), depth = 0, start = -1, count = 0;
   for(int i = p + 1; i < n; i++)
     {
      ushort c = StringGetCharacter(json, i);
      if(c == '{') { if(depth == 0) start = i; depth++; }
      else if(c == '}')
        {
         depth--;
         if(depth == 0 && start >= 0)
           {
            ArrayResize(objs, count + 1);
            objs[count] = StringSubstr(json, start, i - start + 1);
            count++; start = -1;
           }
        }
      else if(c == ']' && depth == 0) break;
     }
   return(count);
  }

//+------------------------------------------------------------------+
//| Minimal JSON readers for the flat, fixed-key slot object         |
//+------------------------------------------------------------------+
string JsonGetStr(string json, string key)
  {
   string pat = "\"" + key + "\":\"";
   int p = StringFind(json, pat);
   if(p < 0) return("");
   p += StringLen(pat);
   int q = StringFind(json, "\"", p);
   if(q < 0) return("");
   return(StringSubstr(json, p, q - p));
  }

double JsonGetNum(string json, string key)
  {
   string pat = "\"" + key + "\":";
   int p = StringFind(json, pat);
   if(p < 0) return(0.0);
   p += StringLen(pat);
   int n = StringLen(json);
   string num = "";
   for(int i = p; i < n; i++)
     {
      ushort c = StringGetCharacter(json, i);
      if((c >= '0' && c <= '9') || c == '.' || c == '-' || c == '+' || c == 'e' || c == 'E')
         num += ShortToString(c);
      else if(StringLen(num) > 0)
         break;
     }
   return(StringToDouble(num));
  }
//+------------------------------------------------------------------+
