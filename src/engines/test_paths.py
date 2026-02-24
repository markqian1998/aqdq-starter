# src/engines/test_paths.py
import numpy as np
import matplotlib.pyplot as plt
from src.engines.paths import gbm_paths_antithetic


def test_paths_981HK():
    # === Market parameters (981.HK example) ===
    S0    = 52.75
    r     = 0.03
    q     = 0.001
    sigma = 0.42474
    r_minus_q = r - q

    # === Time axis ===
    n_days = 245   # 245 trading days ~ 1 year
    times  = np.linspace(1 / 245, n_days / 245, n_days)  # annualised observation times

    # === Monte Carlo path simulation ===
    paths = gbm_paths_antithetic(
        S0=S0,
        times=times,
        r_minus_q=r_minus_q,
        sigma=sigma,
        n_paths=100000,
        seed=42
    )

    # === Sanity checks ===
    print(f"Path matrix shape:       {paths.shape}")
    print(f"First path, first 5 days: {paths[0, :5]}")
    print(f"Second path, first 5 days: {paths[1, :5]}")

    # === Plot a sample of paths ===
    plt.figure(figsize=(10, 6))
    for i in range(2000):
        plt.plot(times, paths[i], lw=1)
    plt.title("981.HK Simulated Price Paths (GBM, Antithetic Variates)")
    plt.xlabel("Time (years)")
    plt.ylabel("Price")
    plt.grid(True)
    plt.show()


if __name__ == "__main__":
    test_paths_981HK()
