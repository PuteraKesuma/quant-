//+------------------------------------------------------------------+
//|  ZStrategy.mq5                                                    |
//|  Port MQL5 dari "Z Strategy" (Pine Script v5, TradingView)        |
//+------------------------------------------------------------------+
//
//  RINGKAS STRATEGINYA
//  Ini strategi FRAKTAL + BREAKOUT. Dia mencari titik balik (swing) lalu
//  memasang stop order JAUH di seberangnya, menunggu harga menembus.
//
//    1. Deteksi fraktal 5-bar. Untuk length=4 -> p=2:
//         puncak : H2 > H1 > H0  dan  H2 > H3 > H4  dan H2 tertinggi dari 4 bar
//         lembah : L2 < L1 < L0  dan  L2 < L3 < L4  dan L2 terendah dari 4 bar
//       (H0 = bar tertutup terakhir, H1 = sebelumnya, dst)
//    2. Puncak  -> pasang BUY STOP  di  (nilai puncak + buffer) - spread
//       Lembah  -> pasang SELL STOP di  (nilai lembah - buffer)
//       Hanya kalau jarak ke harga sekarang >= MinDistance, dan hanya saat FLAT.
//    3. Begitu satu terisi, pending lawannya dibatalkan.
//    4. Keluar: BARU diperiksa setelah candle entry TUTUP. Tiap bar tutup,
//       kalau (close - entry) >= TP -> tutup; kalau <= -SL -> tutup. Kalau
//       belum kena keduanya, pasang SL/TP broker sekali saja sebagai cadangan.
//
//  YANG TIDAK IKUT DIPORT, dan kenapa
//    - line/box/label/table/watermark: itu gambar di chart TradingView,
//      tidak mempengaruhi satu pun keputusan trading. Dibuang semua.
//    - alert(): itu webhook TradingView. Diganti Print() ke log Expert.
//    - ta.atr / ta.percentrank (baris 32-34 Pine): DIHITUNG TAPI TIDAK PERNAH
//      DIPAKAI di Pine aslinya. Tidak diport.
//    - baris 522 Pine memanggil strategy.exit("EXIT_SELL","SELL_STOP",...)
//      di DALAM cabang LONG. Itu no-op (tidak ada posisi short saat long).
//      Kemungkinan salah salin di aslinya. Tidak diport.
//
//  DUA KEJANGGALAN ASLINYA YANG SENGAJA DIPERTAHANKAN
//    a. BUY dipasang di (entry - spread), SELL dipasang di entry TANPA
//       dikurangi/ditambah spread. Tidak simetris. Lihat InpSymmetricSpread
//       kalau mau diluruskan -- default false = persis seperti aslinya.
//    b. Bracket SHORT pakai (SL - spread) dan (TP + spread), bracket LONG
//       tidak. Juga tidak simetris. Dipertahankan.
//
//  SKALA HARGA -- baca ini sebelum mengubah angka apa pun
//    Angka poin Pine dikalikan syminfo.mintick FEED TRADINGVIEW, dan feed
//    emas di sana 3 desimal (0.001). Jadi nilai bawaannya berarti:
//        buffer 6800  -> $6.80 dari titik fraktal
//        spread 500   -> $0.50
//        TP 20000     -> $20.00 pergerakan harga
//        SL 19000     -> $19.00 pergerakan harga
//    Di 0.01 lot emas, $1 pergerakan = $1 P&L. Jadi satu stop loss = -$19,
//    satu take profit = +$20.
//
//    JANGAN memakai _Point broker untuk ini. XAUUSD di FBS 2 desimal
//    (_Point 0.01), yang membuat angka yang sama terbaca 10x lebih besar
//    -- SL jadi seolah $190. Semua jarak strategi memakai InpPineMinTick;
//    _Point hanya dipakai untuk batas stops level broker.
//
//  PERINGATAN RISIKO
//    Rasio TP:SL 20000:19000 hampir 1:1, artinya butuh winrate >51% hanya
//    untuk impas sebelum biaya. Uji dulu, jangan langsung dipasang live.
//
//+------------------------------------------------------------------+
#property copyright "Port dari Pine Script v5 'Z Strategy'"
#property version   "1.00"
#property description "Fraktal + stop order breakout. Port setia dari Pine v5."

#include <Trade/Trade.mqh>

//--- parameter fraktal -------------------------------------------------
input group           "=== Fraktal ==="
input int    InpLength            = 4;      // Panjang fraktal (Pine: length, min 2)

//--- jarak harga -------------------------------------------------------
//
//  PENTING -- ini sumber kesalahan yang mudah terjadi.
//  Angka poin di Pine dikalikan syminfo.mintick, yaitu mintick FEED
//  TRADINGVIEW tempat skrip itu ditulis, BUKAN _Point broker MT5 kita.
//  Feed emas di TradingView memakai 3 desimal (mintick 0.001), sedangkan
//  XAUUSD di FBS 2 desimal (_Point 0.01) -- beda 10x. Memakai _Point
//  membuat SL 19000 terbaca $190 padahal maksudnya $19.
//
//  Karena itu jarak dihitung dari InpPineMinTick, jadi hasilnya identik
//  dengan TradingView berapa pun digit broker.
//
input group           "=== Jarak ==="
input double InpPineMinTick       = 0.001;  // mintick feed Pine (emas TV = 0.001)
input int    InpBufferPoints      = 6800;   // Buffer dari titik fraktal -> $6.80
input int    InpSpreadPoints      = 500;    // Spread                    -> $0.50
input int    InpMinDistancePoints = 6800;   // Jarak minimum entry       -> $6.80
input int    InpTPPoints          = 20000;  // Take profit               -> $20.00
input int    InpSLPoints          = 19000;  // Stop loss                 -> $19.00

//--- eksekusi ----------------------------------------------------------
input group           "=== Eksekusi ==="
input double InpLot               = 0.01;   // Lot
input long   InpMagic             = 20260831; // Magic number
input int    InpSlippage          = 30;     // Slippage (poin)

//--- penyimpangan opsional dari Pine asli ------------------------------
input group           "=== Opsi (default = persis Pine) ==="
input bool   InpSymmetricSpread   = false;  // true: SELL juga digeser spread
input bool   InpProtectOnFill     = false;  // true: pasang SL/TP begitu terisi
input bool   InpVerbose           = true;   // Log rinci ke tab Experts

//--- state -------------------------------------------------------------
CTrade   g_trade;

// Fraktal tersimpan. Di Pine ini `var fractal upper/lower` -- nilainya
// BERTAHAN antar bar, dan flag ready hanya dipadamkan setelah ordernya
// benar-benar dipasang. Kalau fraktal muncul saat masih ada posisi, ready
// tetap true sampai flat. Perilaku itu dipertahankan.
double   g_upperValue = 0.0;
bool     g_upperReady = false;
double   g_lowerValue = 0.0;
bool     g_lowerReady = false;

// Pelacakan posisi (Pine: entryBar / entryPrice / exitPlaced)
datetime g_entryBarTime = 0;
double   g_entryPrice   = 0.0;
bool     g_exitPlaced   = false;
bool     g_inPosition   = false;

datetime g_lastBarTime  = 0;

double   g_point;    // _Point broker -- HANYA untuk batas stops level broker
double   g_tick;     // mintick Pine  -- untuk SEMUA jarak strategi
int      g_digits;

//+------------------------------------------------------------------+
//| Cetak seluruh input saat init.                                    |
//| Bukan hiasan: di proyek ini pernah dua kali EA berjalan dengan    |
//| setelan default diam-diam (setelah .ex5 diganti, input balik ke   |
//| default EA -- BUKAN ke preset), dan sekali berjalan di timeframe  |
//| yang salah. Blok ini yang menangkap keduanya.                     |
//+------------------------------------------------------------------+
void DumpInputs()
{
   Print("=== INPUT AKTIF ZStrategy ===");
   PrintFormat("  Simbol=%s  TF=%s  Digits=%d  Point=%.5f",
               _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period), g_digits, g_point);
   PrintFormat("  Length=%d (p=%d)", InpLength, InpLength / 2);
   PrintFormat("  BufferPoints=%d  SpreadPoints=%d  MinDistancePoints=%d",
               InpBufferPoints, InpSpreadPoints, InpMinDistancePoints);
   PrintFormat("  TPPoints=%d  SLPoints=%d", InpTPPoints, InpSLPoints);
   PrintFormat("  Lot=%.2f  Magic=%d  Slippage=%d", InpLot, InpMagic, InpSlippage);
   PrintFormat("  SymmetricSpread=%s  ProtectOnFill=%s",
               InpSymmetricSpread ? "true" : "false",
               InpProtectOnFill ? "true" : "false");
   PrintFormat("  PineMinTick=%.5f", g_tick);
   // Dicetak dalam DOLAR pergerakan harga, bukan poin, supaya salah skala
   // 10x seperti kejadian pertama langsung kelihatan di log.
   PrintFormat("  -> jarak harga: buffer=$%.2f spread=$%.2f TP=$%.2f SL=$%.2f",
               InpBufferPoints * g_tick, InpSpreadPoints * g_tick,
               InpTPPoints * g_tick, InpSLPoints * g_tick);
   PrintFormat("  -> di %.2f lot: TP kira-kira $%.2f, SL kira-kira $%.2f",
               InpLot, InpTPPoints * g_tick * InpLot * 100.0,
               InpSLPoints * g_tick * InpLot * 100.0);
   Print("=============================");
}

//+------------------------------------------------------------------+
int OnInit()
{
   g_point  = _Point;
   g_digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   g_tick   = InpPineMinTick;

   if(g_tick <= 0.0)
   {
      Print("ZStrategy: InpPineMinTick harus > 0. Init dibatalkan.");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpLength < 2)
   {
      Print("ZStrategy: InpLength minimal 2 (Pine: minval=2). Init dibatalkan.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpLot <= 0.0)
   {
      Print("ZStrategy: InpLot harus > 0. Init dibatalkan.");
      return INIT_PARAMETERS_INCORRECT;
   }

   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(InpSlippage);
   g_trade.SetTypeFillingBySymbol(_Symbol);

   DumpInputs();

   // Pulihkan state kalau EA di-restart saat posisi sedang terbuka.
   SyncPosition();

   g_lastBarTime = iTime(_Symbol, _Period, 0);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   PrintFormat("ZStrategy deinit, alasan=%d", reason);
}

//+------------------------------------------------------------------+
//| Pine math.sign: -1, 0, atau 1. Nilai sama menghasilkan 0, dan itu |
//| penting -- dua high yang identik memutus rantai fraktal.          |
//+------------------------------------------------------------------+
int Sgn(const double x)
{
   if(x > 0.0) return 1;
   if(x < 0.0) return -1;
   return 0;
}

//+------------------------------------------------------------------+
//| Jembatan indeks Pine -> MQL5.                                     |
//|                                                                   |
//| Di Pine, high[0] adalah bar berjalan; seluruh logika entry dijaga |
//| barstate.isconfirmed sehingga efektifnya bekerja pada bar TERTUTUP.|
//| Di MQL5 shift 0 adalah bar yang masih terbentuk. Jadi Pine-indeks  |
//| i = MQL5 shift (i+1). Salah satu offset di sini akan membuat EA    |
//| melihat bar yang belum selesai -- kesalahan yang sudah pernah      |
//| terjadi di proyek ini dan menghasilkan sinyal hantu.               |
//+------------------------------------------------------------------+
double PH(const int i) { return iHigh (_Symbol, _Period, i + 1); }
double PL(const int i) { return iLow  (_Symbol, _Period, i + 1); }
double PC(const int i) { return iClose(_Symbol, _Period, i + 1); }

//+------------------------------------------------------------------+
//| Pine: math.sum(math.sign(high - high[1]), p) digeser k bar.        |
//+------------------------------------------------------------------+
int SumSignHigh(const int k, const int p)
{
   int s = 0;
   for(int i = k; i < k + p; i++) s += Sgn(PH(i) - PH(i + 1));
   return s;
}

int SumSignLow(const int k, const int p)
{
   int s = 0;
   for(int i = k; i < k + p; i++) s += Sgn(PL(i) - PL(i + 1));
   return s;
}

//+------------------------------------------------------------------+
//| Pine: ta.highest(length) / ta.lowest(length) pada bar berjalan.    |
//+------------------------------------------------------------------+
double HighestH(const int length)
{
   double m = PH(0);
   for(int i = 1; i < length; i++) m = MathMax(m, PH(i));
   return m;
}

double LowestL(const int length)
{
   double m = PL(0);
   for(int i = 1; i < length; i++) m = MathMin(m, PL(i));
   return m;
}

//+------------------------------------------------------------------+
//| Posisi milik EA ini (magic + simbol). Pine hanya mengenal satu    |
//| posisi (strategy.position_size), jadi yang pertama ketemu dipakai.|
//+------------------------------------------------------------------+
bool FindPosition(ulong &ticket, ENUM_POSITION_TYPE &type, double &openPrice)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic)  continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)   continue;
      ticket    = t;
      type      = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
bool FindPending(const ENUM_ORDER_TYPE want, ulong &ticket, double &price)
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong t = OrderGetTicket(i);
      if(t == 0) continue;
      if(OrderGetInteger(ORDER_MAGIC) != InpMagic)     continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol)      continue;
      if((ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE) != want) continue;
      ticket = t;
      price  = OrderGetDouble(ORDER_PRICE_OPEN);
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
void CancelPending(const ENUM_ORDER_TYPE want)
{
   ulong  t = 0;
   double p = 0.0;
   while(FindPending(want, t, p))
   {
      if(!g_trade.OrderDelete(t))
      {
         PrintFormat("ZStrategy: gagal batalkan pending #%I64u (%s), retcode=%d",
                     t, EnumToString(want), g_trade.ResultRetcode());
         break;
      }
      if(InpVerbose)
         PrintFormat("ZStrategy: pending %s #%I64u dibatalkan", EnumToString(want), t);
   }
}

//+------------------------------------------------------------------+
//| Broker menolak stop order yang terlalu dekat harga. Pine tidak    |
//| mengenal batasan ini sama sekali; tanpa penjagaan ini order akan  |
//| ditolak diam-diam dan strateginya terlihat "tidak pernah entry".  |
//+------------------------------------------------------------------+
bool StopPriceValid(const ENUM_ORDER_TYPE type, const double price)
{
   long   lvl  = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minD = (double)lvl * g_point;
   double ask  = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid  = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   if(type == ORDER_TYPE_BUY_STOP)  return (price >= ask + minD);
   if(type == ORDER_TYPE_SELL_STOP) return (price <= bid - minD);
   return false;
}

//+------------------------------------------------------------------+
//| Pine strategy.entry(id, stop=X) MENGGANTI order ber-id sama kalau |
//| sudah ada. Jadi: kalau sudah ada pending, ubah harganya; kalau    |
//| belum, pasang baru.                                               |
//+------------------------------------------------------------------+
void PlaceOrUpdateStop(const ENUM_ORDER_TYPE type, const double rawPrice)
{
   double price = NormalizeDouble(rawPrice, g_digits);

   if(!StopPriceValid(type, price))
   {
      if(InpVerbose)
         PrintFormat("ZStrategy: %s @%.*f ditolak -- terlalu dekat harga "
                     "(stops level %d poin)", EnumToString(type), g_digits, price,
                     (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL));
      return;
   }

   ulong  t   = 0;
   double old = 0.0;
   if(FindPending(type, t, old))
   {
      if(MathAbs(old - price) < g_point * 0.5) return;   // sudah di harga itu
      if(!g_trade.OrderModify(t, price, 0.0, 0.0, ORDER_TIME_GTC, 0))
         PrintFormat("ZStrategy: gagal ubah pending #%I64u ke %.*f, retcode=%d",
                     t, g_digits, price, g_trade.ResultRetcode());
      else if(InpVerbose)
         PrintFormat("ZStrategy: %s dipindah %.*f -> %.*f",
                     EnumToString(type), g_digits, old, g_digits, price);
      return;
   }

   bool ok = (type == ORDER_TYPE_BUY_STOP)
             ? g_trade.BuyStop (InpLot, price, _Symbol, 0.0, 0.0, ORDER_TIME_GTC, 0, "Z")
             : g_trade.SellStop(InpLot, price, _Symbol, 0.0, 0.0, ORDER_TIME_GTC, 0, "Z");

   if(!ok)
      PrintFormat("ZStrategy: gagal pasang %s @%.*f, retcode=%d",
                  EnumToString(type), g_digits, price, g_trade.ResultRetcode());
   else
      PrintFormat("ZStrategy: %s dipasang @%.*f", EnumToString(type), g_digits, price);
}

//+------------------------------------------------------------------+
//| Pine baris 335-338: catat bar & harga entry saat posisi terdeteksi.|
//| Tidak dijaga barstate.isconfirmed di aslinya, jadi dijalankan tiap |
//| tick di sini juga.                                                |
//+------------------------------------------------------------------+
void SyncPosition()
{
   ulong             ticket = 0;
   ENUM_POSITION_TYPE type  = POSITION_TYPE_BUY;
   double            open   = 0.0;

   if(FindPosition(ticket, type, open))
   {
      if(!g_inPosition)
      {
         g_inPosition   = true;
         g_entryBarTime = iTime(_Symbol, _Period, 0);
         g_entryPrice   = open;
         g_exitPlaced   = false;

         PrintFormat("ZStrategy: TERISI %s @%.*f  (Pine: 'SUDAH KENA')",
                     type == POSITION_TYPE_BUY ? "BUY" : "SELL", g_digits, open);

         // Pine membatalkan pending lawan begitu posisi terbentuk.
         CancelPending(type == POSITION_TYPE_BUY ? ORDER_TYPE_SELL_STOP
                                                 : ORDER_TYPE_BUY_STOP);

         if(InpProtectOnFill)
            PlaceBracket(ticket, type, open);
      }
   }
   else if(g_inPosition)
   {
      // Pine baris 568-572: reset setelah posisi tertutup.
      g_inPosition   = false;
      g_entryBarTime = 0;
      g_entryPrice   = 0.0;
      g_exitPlaced   = false;
      if(InpVerbose) Print("ZStrategy: posisi tertutup, state di-reset");
   }
}

//+------------------------------------------------------------------+
//| Pine strategy.exit(loss=..., profit=...) -> SL/TP di posisi.       |
//| Perhatikan ketidaksimetrisan aslinya: cabang SHORT memakai         |
//| (sl_points - spread_points) dan (tp_points + spread_points),       |
//| cabang LONG memakai angka polos. Dipertahankan apa adanya.         |
//+------------------------------------------------------------------+
void PlaceBracket(const ulong ticket, const ENUM_POSITION_TYPE type, const double entry)
{
   double sl, tp;

   if(type == POSITION_TYPE_BUY)
   {
      sl = entry - InpSLPoints * g_tick;
      tp = entry + InpTPPoints * g_tick;
   }
   else
   {
      sl = entry + (InpSLPoints - InpSpreadPoints) * g_tick;
      tp = entry - (InpTPPoints + InpSpreadPoints) * g_tick;
   }

   sl = NormalizeDouble(sl, g_digits);
   tp = NormalizeDouble(tp, g_digits);

   if(!g_trade.PositionModify(ticket, sl, tp))
      PrintFormat("ZStrategy: gagal pasang SL/TP (SL=%.*f TP=%.*f), retcode=%d",
                  g_digits, sl, g_digits, tp, g_trade.ResultRetcode());
   else
      PrintFormat("ZStrategy: SL=%.*f TP=%.*f dipasang", g_digits, sl, g_digits, tp);
}

//+------------------------------------------------------------------+
//| Pine baris 113-121: simpan fraktal saat terbentuk.                 |
//+------------------------------------------------------------------+
void DetectFractals()
{
   int p = InpLength / 2;
   if(p < 1) return;

   // butuh PH(2p) -> shift 2p+1, plus cadangan
   if(Bars(_Symbol, _Period) < InpLength + 2 * p + 3) return;

   bool bullf = (SumSignHigh(0, p) == -p)
             && (SumSignHigh(p, p) ==  p)
             && (PH(p) == HighestH(InpLength));

   bool bearf = (SumSignLow(0, p) ==  p)
             && (SumSignLow(p, p) == -p)
             && (PL(p) == LowestL(InpLength));

   if(bullf)
   {
      g_upperValue = PH(p);
      g_upperReady = true;
      if(InpVerbose)
         PrintFormat("ZStrategy: fraktal PUNCAK @%.*f", g_digits, g_upperValue);
   }

   if(bearf)
   {
      g_lowerValue = PL(p);
      g_lowerReady = true;
      if(InpVerbose)
         PrintFormat("ZStrategy: fraktal LEMBAH @%.*f", g_digits, g_lowerValue);
   }
}

//+------------------------------------------------------------------+
//| Pine baris 126-135 dan 230-237.                                   |
//| Syarat aslinya: ready DAN position_size==0 DAN bar terkonfirmasi.  |
//+------------------------------------------------------------------+
void TryPlaceEntries()
{
   if(g_inPosition) return;                      // Pine: strategy.position_size == 0

   double buffer  = InpBufferPoints * g_tick;
   double spread  = InpSpreadPoints * g_tick;
   double minDist = InpMinDistancePoints * g_tick;
   double close0  = PC(0);

   if(g_upperReady)
   {
      double buyEntry   = g_upperValue + buffer;
      double buyTrigger = buyEntry - spread;     // asimetri asli dipertahankan

      if(MathAbs(buyEntry - close0) >= minDist)
         PlaceOrUpdateStop(ORDER_TYPE_BUY_STOP, buyTrigger);
      else if(InpVerbose)
         PrintFormat("ZStrategy: BUY dilewati, jarak %.*f < minimum %.*f",
                     g_digits, MathAbs(buyEntry - close0), g_digits, minDist);

      g_upperReady = false;                      // Pine baris 225
   }

   if(g_lowerReady)
   {
      double sellEntry   = g_lowerValue - buffer;
      // Pine TIDAK menggeser SELL dengan spread. InpSymmetricSpread=true
      // meluruskannya; default false = persis aslinya.
      double sellTrigger = InpSymmetricSpread ? sellEntry + spread : sellEntry;

      if(MathAbs(sellEntry - close0) >= minDist)
         PlaceOrUpdateStop(ORDER_TYPE_SELL_STOP, sellTrigger);
      else if(InpVerbose)
         PrintFormat("ZStrategy: SELL dilewati, jarak %.*f < minimum %.*f",
                     g_digits, MathAbs(sellEntry - close0), g_digits, minDist);

      g_lowerReady = false;                      // Pine baris 327
   }
}

//+------------------------------------------------------------------+
//| Pine baris 502-539.                                               |
//| Kuncinya: pemeriksaan BARU aktif setelah candle entry TUTUP        |
//| (candleEntryClosed = bar_index > entryBar). Selama candle entry    |
//| masih berjalan, posisi TIDAK dijaga apa-apa -- itu memang perilaku |
//| aslinya. InpProtectOnFill=true kalau mau ditutup lubangnya.        |
//+------------------------------------------------------------------+
void ManageExit()
{
   if(!g_inPosition) return;

   ulong             ticket = 0;
   ENUM_POSITION_TYPE type  = POSITION_TYPE_BUY;
   double            open   = 0.0;
   if(!FindPosition(ticket, type, open)) return;

   if(iTime(_Symbol, _Period, 0) <= g_entryBarTime) return;   // candle entry belum tutup

   double close0  = PC(0);
   double tpPrice = InpTPPoints * g_tick;
   double slPrice = InpSLPoints * g_tick;

   double pnl = (type == POSITION_TYPE_BUY) ? (close0 - g_entryPrice)
                                            : (g_entryPrice - close0);

   if(pnl >= tpPrice)
   {
      if(g_trade.PositionClose(ticket))
      {
         PrintFormat("ZStrategy: CUT PROFIT, pnl harga %.*f", g_digits, pnl);
         g_exitPlaced = false;
      }
      return;
   }

   if(pnl <= -slPrice)
   {
      if(g_trade.PositionClose(ticket))
      {
         PrintFormat("ZStrategy: CUT LOSS, pnl harga %.*f", g_digits, pnl);
         g_exitPlaced = false;
      }
      return;
   }

   if(!g_exitPlaced)
   {
      PlaceBracket(ticket, type, g_entryPrice);
      g_exitPlaced = true;
   }
}

//+------------------------------------------------------------------+
void OnTick()
{
   // Tiap tick: Pine memakai calc_on_every_tick, dan pencatatan entry
   // serta pembatalan pending lawan memang tidak dijaga isconfirmed.
   SyncPosition();

   // Sisanya hanya pada bar baru -- padanan barstate.isconfirmed.
   datetime t = iTime(_Symbol, _Period, 0);
   if(t == g_lastBarTime) return;
   g_lastBarTime = t;

   DetectFractals();
   TryPlaceEntries();
   ManageExit();
}
//+------------------------------------------------------------------+
