"""Forecast engine v4: pooled models trained across the whole universe.

The v3 ensemble fit small models per symbol on ~400 rows and measured worse
than the historical base rate. v4 instead:

* forecasts volatility (the forecastable part of returns) and builds the
  return range and drop risk from it (:mod:`.volatility`);
* trains one model per horizon across hundreds of stocks and ten years of
  history on scale-free features (:mod:`.features`, :mod:`.model`), for
  absolute direction and for relative performance against the market;
* is evaluated walk-forward by date before it is trusted
  (:mod:`.evaluate`), and ships its measured skill with the model artifact.
"""
