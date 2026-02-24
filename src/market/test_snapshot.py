from datetime import date
from src.market.snapshot import EquitySnapshot

# Create a snapshot with a flat 3% risk-free rate and 42.7% vol
mkt = EquitySnapshot(
    today=date(2025, 8, 20),
    spot=52.75,
    flat_r=0.03,      # 3.0% continuously compounded risk-free rate
    div_yield=0.001,  # 0.1% continuously compounded dividend yield
    vol=0.42724
)

T  = 0.5              # 6-month maturity
df = mkt.df(T)        # discount factor: e^{-rT}
mu = mkt.fwd_drift()  # risk-neutral drift: r - q

print("df: ", df)
print("mu: ", mu)
