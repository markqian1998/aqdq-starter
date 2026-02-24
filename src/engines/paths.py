# src/engines/paths.py
"""GBM path simulation with antithetic variates.

Under the risk-neutral measure, the stock price S_t follows Geometric
Brownian Motion (GBM):

    dS_t = S_t * ((r - q) dt + sigma dW_t)

where:
    r     = risk-free rate
    q     = continuous dividend yield
    sigma = annualised volatility
    W_t   = standard Brownian motion

After discretisation, the log-return over each step is normally distributed:

    ln S_{t+dt} = ln S_t + ((r-q) - 0.5*sigma^2)*dt + sigma*sqrt(dt)*Z,
    Z ~ N(0, 1)
"""

from __future__ import annotations
import numpy as np


def gbm_paths_antithetic(
    S0: float,              # initial stock price
    times: np.ndarray,      # observation time points (year-fractions from today, strictly increasing)
    r_minus_q: float,       # risk-neutral drift: r - q
    sigma: float,           # annualised volatility
    n_paths: int = 1000000, # number of simulated paths
    seed: int = 42          # random seed (fixed for reproducibility and stable Greeks)
) -> np.ndarray:
    """Simulate GBM price paths using antithetic variates.

    Returns an array of shape (n_paths, n_steps) where each row is one
    simulated price path and each column corresponds to one observation date.

    Antithetic variates: for every draw Z we also include -Z, which halves
    the variance of the Monte Carlo estimator at no extra computation cost.
    """
    rng = np.random.default_rng(seed)
    # A seeded generator ensures identical random numbers across runs,
    # which is critical for stable finite-difference Greeks.

    n_steps = len(times)

    # Compute per-step mean and standard deviation of log-returns
    dt  = np.diff(np.r_[0.0, times])                 # time step sizes Δt
    mu  = (r_minus_q - 0.5 * sigma**2) * dt          # expected log-return per step
    sig = sigma * np.sqrt(dt)                        # log-return std dev per step

    # Generate antithetic standard normal draws.
    # We generate half the paths with +Z and the other half with -Z for variance reduction.
    half = (n_paths + 1) // 2
    Z = rng.standard_normal(size=(half, n_steps))
    Z = np.vstack([Z, -Z])[:n_paths]                 # antithetic mirror

    # Simulate log-prices iteratively
    logS = np.full((n_paths, n_steps), np.log(S0))
    for j in range(n_steps):
        prev = logS[:, j-1] if j > 0 else np.log(S0)
        logS[:, j] = prev + mu[j] + sig[j] * Z[:, j]
        # new log-price = old log-price + drift*dt + volatility*sqrt(dt)*Z

    # Return price paths (exponentiate log-prices).
    # Output shape: (n_paths, n_steps); each row is a path, each column is an observation date.
    return np.exp(logS)

# Typical uses of this output:
#   - Monte Carlo pricing:   compute expected payoff across paths for AQ/DQ products
#   - Greeks:                re-run with bumped S0 or sigma and apply finite differences
#   - Risk management:       derive distributions, VaR, stress scenarios
