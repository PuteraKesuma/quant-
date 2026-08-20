//+------------------------------------------------------------------+
//| EternaVote.mq5 - eterna sebagai ENSEMBLE VOTING, untuk Strategy   |
//|                  Tester. Verifikasi tick-sungguhan atas temuan    |
//|                  research/eterna_ensemble_final.py                |
//|                                                                  |
//| KENAPA ADA                                                       |
//| Simulasi Python (bar H1, biaya rata $0.50) menemukan bahwa        |
//| memakai 8 varian ATR sebagai PEMILIH -- bukan 8 posisi paralel -- |
//| mengalahkan konfigurasi tunggal yang kita jalankan live:          |
//|      TUNGGAL   net $1699  PF 1.22  Ret/DD 3.97  WR 30.2%          |
//|      >=7/8     net $2325  PF 1.28  Ret/DD 4.80  WR 36.4%          |
//| dan itu berdiri di DATARAN (6/8, 7/8, 8/8 semua serupa), bukan    |
//| puncak sendirian -- 1/8..5/8 semuanya jelas lebih buruk.          |
//|                                                                  |
//| Tapi angka Python memakai fill di CLOSE bar dan biaya rata. EA    |
//| ini menjalankan logika yang sama lewat mesin yang sama dengan     |
//| Semi Marti (tiap tick, spread nyata, urutan fill nyata), supaya   |
//| keputusan deploy tidak pernah bersandar pada simulasi bar saja.   |
//|                                                                  |
//| KENAPA VOTING, BUKAN 8 POSISI PARALEL                            |
//| Lot minimum 0.01 dan cap risiko $70 per trade. 8 posisi paralel   |
//| berarti risiko serentak sampai $560 -- 104% dari akun $538. Tidak |
//| muat. Voting memberi kualitas sinyal ensemble dengan biaya SATU   |
//| posisi 0.01 lot.                                                  |
//|                                                                  |
//| ANGGOTA (dipilih A PRIORI, bukan hasil pencarian di data uji)     |
//| atr_period {10,14,20,24} x mult_trend {3.8,5.0}, entry x1.8.      |
//| Rentang 10..24 diambil dari docstring EternaStrategy yang mencatat|
//| dataran sehat 10..24 dengan 16 di tengahnya.                      |
//|                                                                  |
//| ATURAN                                                           |
//|   ready[j] = arah entry-Supertrend anggota j, HANYA kalau         |
//|              trend-Supertrend anggota itu setuju; selain itu 0    |
//|   suara bersih = sum(ready)/8                                     |
//|   konsensus    = +1 kalau >= ambang, -1 kalau <= -ambang, 0 lain  |
//|   MASUK  saat konsensus BERUBAH ke sisi baru dan kita flat        |
//|   KELUAR broker SL/TP, atau konsensus berubah ke sisi berlawanan  |
//|   SL = ekstrem `struct` bar tertutup, struct = MEDIAN atr_period  |
//|        anggota yang setuju; TP = 4x jarak itu                     |
//+------------------------------------------------------------------+
#property strict

#include <Trade\Trade.mqh>

input double InpLot         = 0.01;
input double InpMultEntry   = 1.8;    // sama untuk semua anggota
input double InpThreshold   = 0.875;  // 7/8 suara bersih (tengah dataran)
input double InpTPRatio     = 4.0;
input double InpMinSLDist   = 0.30;
input double InpRiskCapUSD  = 70.0;   // hasil sweep 2026-08-20
input int    InpMagic       = 920628; // BEDA dari eterna tunggal (920627)
input int    InpHistBars    = 5000;
input bool   InpDebug       = false;

// 8 anggota = 4 periode ATR x 2 mult tren
int    ATRP[4] = {10, 14, 20, 24};
double MTS [2] = {3.8, 5.0};
#define NMEM 8

CTrade   trade;
datetime g_last_bar = 0;

// MQL5 hanya mengizinkan dimensi PERTAMA yang dinamis, jadi `int d[4][]` ilegal.
// Bungkus tiap deret arah dalam struct -- ini bentuk yang didukung.
struct DirSeries { int d[]; };

//+------------------------------------------------------------------+
//| Supertrend -- identik dengan EternaBot.mq5 dan dengan             |
//| EternaStrategy._supertrend di sisi Python.                        |
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
      fu = (bu < fu || close[i-1] > fu) ? bu : fu;
      fl = (bl > fl || close[i-1] < fl) ? bl : fl;
      if(close[i] > fu)      d = 1;
      else if(close[i] < fl) d = -1;
      dir[i] = d;
     }
   return started;
  }

int MyPositionType()
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

int g_atr[4] = {INVALID_HANDLE, INVALID_HANDLE, INVALID_HANDLE, INVALID_HANDLE};

int OnInit()
  {
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(50);
   // Handle dibuat SEKALI. Membuat iATR lalu IndicatorRelease tiap tick tidak aman
   // -- handle indikator MT5 dihitung ASINKRON, jadi CopyBuffer tepat sesudahnya
   // bisa mengembalikan buffer belum siap. ATR bernilai 0 merusak status band
   // Supertrend dan menghasilkan entry yang melanggar gate tren sendiri.
   for(int p = 0; p < 4; p++)
     {
      g_atr[p] = iATR(_Symbol, PERIOD_H1, ATRP[p]);
      if(g_atr[p] == INVALID_HANDLE)
        {
         PrintFormat("ERROR: gagal membuat handle ATR%d", ATRP[p]);
         return INIT_FAILED;
        }
     }
   PrintFormat("EternaVote init: %d anggota, ambang %.3f (>=%.0f/8), TP 1:%.0f, cap $%.0f",
               NMEM, InpThreshold, MathCeil(InpThreshold * NMEM), InpTPRatio, InpRiskCapUSD);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   for(int p = 0; p < 4; p++)
      if(g_atr[p] != INVALID_HANDLE)
         IndicatorRelease(g_atr[p]);
  }

void OnTick()
  {
   datetime bt = iTime(_Symbol, PERIOD_H1, 1);
   if(bt == 0 || bt == g_last_bar)
      return;

   int n = InpHistBars;
   double high[], low[], close[];
   ArraySetAsSeries(high, false);
   ArraySetAsSeries(low, false);
   ArraySetAsSeries(close, false);

   if(CopyHigh (_Symbol, PERIOD_H1, 1, n, high)  != n) return;
   if(CopyLow  (_Symbol, PERIOD_H1, 1, n, low)   != n) return;
   if(CopyClose(_Symbol, PERIOD_H1, 1, n, close) != n) return;

   // ---- indikator: 4 ATR, 4 Supertrend entry, 8 Supertrend tren ----
   DirSeries dEntry[4];      // dEntry[p]      : entry ST untuk ATRP[p], mult 1.8
   DirSeries dTrend[8];      // dTrend[p*2+m]  : trend ST untuk ATRP[p], MTS[m]
   for(int p = 0; p < 4; p++)
     {
      double atrBuf[];
      ArraySetAsSeries(atrBuf, false);
      int got = CopyBuffer(g_atr[p], 0, 1, n, atrBuf);   // handle dari OnInit
      if(got != n) return;

      if(!SupertrendDirs(high, low, close, atrBuf, n, InpMultEntry, dEntry[p].d)) return;
      for(int m = 0; m < 2; m++)
         if(!SupertrendDirs(high, low, close, atrBuf, n, MTS[m], dTrend[p*2+m].d)) return;
     }

   g_last_bar = bt;

   // ---- suara pada bar tertutup terakhir (n-1) dan sebelumnya (n-2) ----
   // Konsensus dihitung di KEDUA bar supaya "perubahan konsensus" terdeteksi
   // tanpa menyimpan state -- jadi EA ini aman kalau di-restart.
   int cons[2];
   for(int k = 0; k < 2; k++)
     {
      int idx = n - 1 - k;
      int net = 0;
      for(int p = 0; p < 4; p++)
         for(int m = 0; m < 2; m++)
           {
            int de = dEntry[p].d[idx];
            int dt = dTrend[p*2+m].d[idx];
            if(de == dt)
               net += de;                       // ready = +1/-1, selain itu 0
           }
      double frac = (double)net / (double)NMEM;
      cons[k] = (frac >= InpThreshold) ? 1 : ((frac <= -InpThreshold) ? -1 : 0);
     }

   int side = cons[0];
   int prev = cons[1];
   int held = MyPositionType();                 // -1 none, 0 buy, 1 sell

   bool changed = (side != 0 && side != prev);

   // konsensus berbalik -> tutup lebih awal (cermin flip berlawanan di eterna)
   if(held >= 0)
     {
      int wantType = (side == 1) ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
      if(changed && wantType != held)
        {
         if(InpDebug) Print("konsensus berbalik -> tutup");
         CloseMine();
        }
      return;
     }

   if(!changed)
      return;

   // struct = MEDIAN atr_period anggota yang setuju di bar n-1.
   // Array DINAMIS dan di-resize tepat sebesar nAgree -- kalau memakai array
   // tetap ukuran 8, ArraySort ikut mengurutkan slot kosong (nol) dan median
   // jadi salah.
   int agreeATR[];
   int nAgree = 0;
   ArrayResize(agreeATR, NMEM);
   for(int p = 0; p < 4; p++)
      for(int m = 0; m < 2; m++)
        {
         int de = dEntry[p].d[n-1];
         int dt = dTrend[p*2+m].d[n-1];
         if(de == dt && de == side)
            agreeATR[nAgree++] = ATRP[p];
        }
   if(nAgree == 0)
      return;
   ArrayResize(agreeATR, nAgree);
   ArraySort(agreeATR);
   // np.median: kalau genap, rata-rata dua nilai tengah; int() memotong.
   // Ditiru persis supaya sinyal MQL5 dan Python identik.
   double med = (nAgree % 2 == 1)
                ? (double)agreeATR[nAgree / 2]
                : ((double)agreeATR[nAgree / 2 - 1] + (double)agreeATR[nAgree / 2]) / 2.0;
   int structBars = (int)med;

   double px = close[n-1];
   int lo = MathMax(0, n - structBars);
   double raw = (side == 1) ? low[lo] : high[lo];
   for(int i = lo; i < n; i++)
     {
      if(side == 1) raw = MathMin(raw, low[i]);
      else          raw = MathMax(raw, high[i]);
     }

   double dist = MathAbs(px - raw);
   if(dist < InpMinSLDist)
     {
      if(InpDebug) PrintFormat("stop terlalu rapat (%.2f) -> lewati", dist);
      return;
     }

   if(InpRiskCapUSD > 0.0)
     {
      double risk = dist * InpLot * SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
      if(risk > InpRiskCapUSD)
        {
         if(InpDebug) PrintFormat("cap risiko: %.2f > %.2f -> lewati", risk, InpRiskCapUSD);
         return;
        }
     }

   double sl = (side == 1) ? px - dist : px + dist;
   double tp = (side == 1) ? px + InpTPRatio * dist : px - InpTPRatio * dist;
   int dg = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   sl = NormalizeDouble(sl, dg);
   tp = NormalizeDouble(tp, dg);

   bool ok = (side == 1) ? trade.Buy(InpLot, _Symbol, 0.0, sl, tp, "eternavote")
                         : trade.Sell(InpLot, _Symbol, 0.0, sl, tp, "eternavote");
   if(!ok)
      PrintFormat("ERROR entry gagal rc=%u %s", trade.ResultRetcode(), trade.ResultComment());
   else if(InpDebug)
      PrintFormat("%s @ %.2f sl=%.2f tp=%.2f dist=%.2f struct=%d setuju=%d",
                  (side == 1 ? "BUY" : "SELL"), px, sl, tp, dist, structBars, nAgree);
  }
//+------------------------------------------------------------------+
