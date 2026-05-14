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
from typing import Callable
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


def gbm_paths_local_vol_antithetic(
    S0: float,
    times: np.ndarray,
    r_minus_q: float,
    vol_fn: Callable[[float, np.ndarray], np.ndarray],
    n_paths: int = 1000000,
    seed: int = 42,
) -> np.ndarray:
    """Simulate log-Euler paths with a state/time dependent local vol.

    vol_fn receives (step_time, previous_spot_vector) and returns an annualised
    volatility vector. Antithetic normal draws are preserved so finite-
    difference Greeks remain stable, matching the flat-vol path generator's
    Monte Carlo convention.
    """
    rng = np.random.default_rng(seed)
    n_steps = len(times)
    dt = np.diff(np.r_[0.0, times])

    half = (n_paths + 1) // 2
    Z = rng.standard_normal(size=(half, n_steps))
    Z = np.vstack([Z, -Z])[:n_paths]

    logS = np.full((n_paths, n_steps), np.log(S0))
    prev_log = np.full(n_paths, np.log(S0), dtype=float)

    for j in range(n_steps):
        prev_spot = np.exp(prev_log)
        vol = np.asarray(vol_fn(float(times[j]), prev_spot), dtype=float)
        vol = np.maximum(vol, 1e-6)
        mu = (r_minus_q - 0.5 * vol**2) * dt[j]
        sig = vol * np.sqrt(dt[j])
        next_log = prev_log + mu + sig * Z[:, j]
        logS[:, j] = next_log
        prev_log = next_log

    return np.exp(logS)

# Typical uses of this output:
#   - Monte Carlo pricing:   compute expected payoff across paths for AQ/DQ products
#   - Greeks:                re-run with bumped S0 or sigma and apply finite differences
#   - Risk management:       derive distributions, VaR, stress scenarios
