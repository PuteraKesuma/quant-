"""How high can Sharpe actually go? The math that governs it (Fundamental Law): combining N edges each
with Sharpe s and average pairwise correlation rho gives
      Sharpe_combined = s * sqrt(N) / sqrt(1 + (N-1)*rho)
Key consequence: if rho>0, there is a HARD CEILING as N->inf  ->  Sharpe_max = s / sqrt(rho),
no matter how many similar edges you stack. To reach Sharpe 2-3 you need either near-ZERO correlation
across MANY edges, or far higher FREQUENCY (intraday = more independent bets/yr). Shows where our book
(s~0.5, ~3 edges, rho~0.15) sits, and what 2-3 requires.

Run: python research/sharpe_scaling.py
"""
import numpy as np

def comb(s, N, rho):
    return s * np.sqrt(N) / np.sqrt(1 + (N - 1) * rho)

print("Combined Sharpe from N edges (each Sharpe s=0.5):\n")
print(f"  {'N edges':>8} | rho=0.00   rho=0.10   rho=0.20   rho=0.30")
for N in (1, 3, 5, 8, 12, 20, 36, 100):
    row = "  ".join(f"{comb(0.5, N, r):>7.2f}" for r in (0.0, 0.10, 0.20, 0.30))
    print(f"  {N:>8} |   {row}")

print("\nHARD CEILING as N->inf  (Sharpe_max = s/sqrt(rho)):")
for r in (0.30, 0.20, 0.10, 0.05, 0.02):
    print(f"  rho={r:.2f}: max Sharpe = {0.5/np.sqrt(r):.2f}  (adding more SIMILAR edges can NEVER beat this)")

print("\n--- where WE are ---")
print(f"  our book: ~3 edges (trend/carry/reversal), avg Sharpe ~0.5, residual corr ~0.15 -> combined ~{comb(0.5,3,0.15):.2f} (measured 0.81)")
print(f"  even 20 such edges at rho=0.15 -> only {comb(0.5,20,0.15):.2f}; the 0.15 corr CAPS us near {0.5/np.sqrt(0.15):.2f}")

print("\n--- what Sharpe 2 / 3 REQUIRES ---")
for target in (1.0, 1.5, 2.0, 3.0):
    # need either many near-zero-corr edges, OR higher freq. Show N needed at rho=0.05 and rho=0.
    n05 = None
    for N in range(1, 2000):
        if comb(0.5, N, 0.05) >= target: n05 = N; break
    n0 = int(np.ceil((target / 0.5) ** 2))
    cap05 = 0.5/np.sqrt(0.05)
    n05s = f"{n05} edges @rho0.05" if n05 else f"IMPOSSIBLE @rho0.05 (ceiling {cap05:.2f})"
    print(f"  Sharpe {target:.1f}: needs {n0} PERFECTLY-uncorrelated edges, or {n05s}")

print("\n=> Sharpe 2-3 on DAILY bars w/ a few correlated edges is mathematically out of reach.")
print("   The only real levers: (1) genuinely DIFFERENT (near-0-corr) edges, (2) higher FREQUENCY")
print("   (intraday = 10-50x more independent bets/yr -> sqrt(breadth) lifts Sharpe). Retail daily")
print("   diversified funds (AHL/Winton) run ~0.5-1.0; ~1.3 is already excellent; 2-3 = HFT/Medallion.")
print("DONE")
