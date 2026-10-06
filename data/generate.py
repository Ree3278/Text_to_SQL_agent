"""Generate a synthetic, Citi Bike-shaped dataset.

Deterministic: same SEED -> identical data. Run from the repo root:

    python -m data.generate

Output:
    data/parquet/{stations,trips,daily_weather}.parquet
    data/bike.duckdb   (the file the app opens read-only)

The schema mirrors the real Citi Bike trip export (ride_id, rideable_type,
started_at, ended_at, member_casual, ...), so swapping in the real data later
is mostly a loader job, not an app change.

The data has *planted patterns* so questions have interesting answers:
  - members commute (peaks ~8am and ~5-6pm on weekdays); casuals ride midday/weekends
  - rain hurts casual riders much more than members
  - warm weather -> more trips; summer >> winter
  - electric bikes are faster (shorter durations)
  - Manhattan stations are busiest; most trips end in the same borough they start
"""
from __future__ import annotations

import itertools
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

SEED = 42
START_DATE = "2025-01-01"
END_DATE = "2025-12-31"

DATA_DIR = Path(__file__).resolve().parent
PARQUET_DIR = DATA_DIR / "parquet"
DB_PATH = DATA_DIR / "bike.duckdb"

# --------------------------------------------------------------------------- stations
# borough: (n_stations, centre_lat, centre_lon, popularity, spread_deg, street_pool_a, street_pool_b)
BOROUGHS = {
    "Manhattan": (
        30, 40.758, -73.985, 2.0, 0.045,
        ["W 42 St", "W 34 St", "E 14 St", "W 57 St", "E 23 St", "W 72 St", "E 86 St", "W 20 St",
         "Houston St", "Canal St", "W 4 St", "E 47 St"],
        ["8 Ave", "7 Ave", "6 Ave", "5 Ave", "Broadway", "1 Ave", "2 Ave", "3 Ave", "Lexington Ave",
         "9 Ave", "Amsterdam Ave", "Park Ave"],
    ),
    "Brooklyn": (
        20, 40.690, -73.975, 1.0, 0.040,
        ["Bedford Ave", "Flushing Ave", "Atlantic Ave", "Court St", "Smith St", "Fulton St",
         "Nostrand Ave", "Driggs Ave", "Kent Ave", "Union Ave"],
        ["N 7 St", "Dean St", "Pacific St", "DeKalb Ave", "Myrtle Ave", "Lafayette Ave",
         "Berry St", "Greenpoint Ave"],
    ),
    "Queens": (
        8, 40.745, -73.930, 0.5, 0.030,
        ["Vernon Blvd", "Jackson Ave", "Northern Blvd", "Astoria Blvd", "Queens Plaza"],
        ["31 St", "21 St", "44 Dr", "11 St", "Crescent St"],
    ),
    "Bronx": (
        4, 40.825, -73.920, 0.3, 0.020,
        ["Grand Concourse", "Willis Ave", "E 138 St"],
        ["E 149 St", "Third Ave", "Jackson Ave"],
    ),
}


def make_stations(rng: np.random.Generator) -> pd.DataFrame:
    rows, sid = [], 1
    for borough, (n, lat0, lon0, pop, spread, pool_a, pool_b) in BOROUGHS.items():
        pairs = [f"{a} & {b}" for a, b in itertools.product(pool_a, pool_b)]
        names = rng.choice(pairs, size=n, replace=False)
        for name in names:
            popularity = pop * rng.lognormal(0, 0.5)
            rows.append(
                dict(
                    station_id=sid,
                    station_name=str(name),
                    borough=borough,
                    latitude=round(lat0 + rng.normal(0, spread), 5),
                    longitude=round(lon0 + rng.normal(0, spread), 5),
                    capacity=int(np.clip(round(20 + 12 * popularity + rng.normal(0, 4)), 15, 70)),
                    _popularity=popularity,  # internal; dropped before saving
                )
            )
            sid += 1
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- weather
def make_weather(rng: np.random.Generator) -> pd.DataFrame:
    dates = pd.date_range(START_DATE, END_DATE, freq="D")
    doy = dates.dayofyear.to_numpy()
    seasonal = 12.5 - 14.5 * np.cos(2 * np.pi * (doy - 15) / 365)  # coldest mid-Jan, hottest mid-Jul
    # smooth day-to-day noise so weather has some persistence
    noise = pd.Series(rng.normal(0, 3.5, len(dates))).rolling(3, min_periods=1).mean().to_numpy()
    temp = seasonal + noise
    rainy = rng.random(len(dates)) < 0.28
    precip = np.where(rainy, rng.exponential(8.0, len(dates)), 0.0)
    return pd.DataFrame(
        dict(
            date=dates.date,
            avg_temp_c=np.round(temp, 1),
            precipitation_mm=np.round(precip, 1),
        )
    )


# --------------------------------------------------------------------------- trips
def _norm(w):
    w = np.asarray(w, dtype=float)
    return w / w.sum()


HOUR_WEIGHTS = {
    ("member", "weekday"): _norm([.3, .2, .1, .1, .2, .8, 2.5, 6, 9, 5, 3, 3.5, 4, 3.5, 3, 3.5, 5, 8, 9, 5.5, 3.5, 2.5, 1.5, .8]),
    ("casual", "weekday"): _norm([.4, .2, .1, .1, .1, .3, .8, 1.5, 2.5, 3, 4, 5, 6, 6.5, 6.5, 6.5, 6.5, 6, 5, 3.5, 2.5, 1.8, 1.2, .7]),
    ("member", "weekend"): _norm([.8, .5, .3, .2, .2, .4, .8, 1.5, 2.5, 4, 5.5, 6.5, 7, 7, 7, 6.5, 6, 5, 4, 3, 2.5, 2, 1.5, 1.2]),
    ("casual", "weekend"): _norm([.9, .6, .4, .2, .2, .3, .6, 1.2, 2.2, 3.8, 5.5, 7, 7.5, 7.5, 7.5, 7, 6.5, 5.5, 4.5, 3.5, 2.8, 2.2, 1.7, 1.3]),
}

# median trip minutes by (rider, bike)
MEDIAN_MIN = {
    ("member", "classic_bike"): 11, ("member", "electric_bike"): 9,
    ("casual", "classic_bike"): 22, ("casual", "electric_bike"): 16,
}
P_ELECTRIC = {"member": 0.40, "casual": 0.50}
P_LOOP = {"member": 0.02, "casual": 0.12}  # trip returns to the start station
BASE_DAILY = {"member": 550, "casual": 250}
WEEKEND_MULT = {"member": 0.70, "casual": 1.50}
RAIN_PENALTY = {"member": 0.30, "casual": 0.65}  # max fraction of trips lost on a very wet day


def make_trips(rng: np.random.Generator, stations: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for day in weather.itertuples(index=False):
        d = pd.Timestamp(day.date)
        is_weekend = d.dayofweek >= 5
        temp_factor = 1 / (1 + np.exp(-(day.avg_temp_c - 8) / 6))
        wetness = min(day.precipitation_mm / 10, 1.0) if day.precipitation_mm > 0.1 else 0.0
        for rider in ("member", "casual"):
            lam = (
                BASE_DAILY[rider]
                * temp_factor
                * (WEEKEND_MULT[rider] if is_weekend else 1.0)
                * (1 - RAIN_PENALTY[rider] * wetness)
            )
            n = int(rng.poisson(lam))
            if n == 0:
                continue
            hours = rng.choice(24, size=n, p=HOUR_WEIGHTS[(rider, "weekend" if is_weekend else "weekday")])
            seconds_in_hour = rng.integers(0, 3600, size=n)
            started = d + pd.to_timedelta(hours * 3600 + seconds_in_hour, unit="s")
            parts.append(pd.DataFrame({"started_at": started, "member_casual": rider}))
    trips = pd.concat(parts, ignore_index=True).sort_values("started_at", ignore_index=True)
    n = len(trips)

    # bike type + duration
    is_member = (trips["member_casual"] == "member").to_numpy()
    p_elec = np.where(is_member, P_ELECTRIC["member"], P_ELECTRIC["casual"])
    electric = rng.random(n) < p_elec
    trips["rideable_type"] = np.where(electric, "electric_bike", "classic_bike")
    median = np.array(
        [MEDIAN_MIN[(r, b)] for r, b in zip(trips["member_casual"], trips["rideable_type"])], dtype=float
    )
    duration_sec = np.clip(np.exp(rng.normal(np.log(median * 60), 0.55)), 60, 3 * 3600).astype(int)
    trips["ended_at"] = trips["started_at"] + pd.to_timedelta(duration_sec, unit="s")

    # stations: popularity-weighted start, proximity-weighted end
    pop = stations["_popularity"].to_numpy()
    xy = stations[["latitude", "longitude"]].to_numpy()
    dlat = (xy[:, None, 0] - xy[None, :, 0]) * 111.0  # km per degree latitude
    dlon = (xy[:, None, 1] - xy[None, :, 1]) * 84.0   # km per degree longitude at NYC
    km = np.sqrt(dlat**2 + dlon**2)
    end_w = pop[None, :] * np.exp(-km / 3.0)
    end_cdf = np.cumsum(end_w / end_w.sum(axis=1, keepdims=True), axis=1)

    start_idx = rng.choice(len(stations), size=n, p=pop / pop.sum())
    u = rng.random(n)
    end_idx = np.minimum((end_cdf[start_idx] < u[:, None]).sum(axis=1), len(stations) - 1)
    loop = rng.random(n) < np.where(is_member, P_LOOP["member"], P_LOOP["casual"])
    end_idx = np.where(loop, start_idx, end_idx)

    sid = stations["station_id"].to_numpy()
    trips["start_station_id"] = sid[start_idx]
    trips["end_station_id"] = sid[end_idx]
    trips["trip_duration_sec"] = duration_sec
    trips.insert(0, "ride_id", [f"R{i:08d}" for i in range(1, n + 1)])
    return trips[
        ["ride_id", "rideable_type", "started_at", "ended_at", "start_station_id", "end_station_id",
         "member_casual", "trip_duration_sec"]
    ]


# --------------------------------------------------------------------------- main
def build() -> None:
    rng = np.random.default_rng(SEED)
    stations = make_stations(rng)
    weather = make_weather(rng)
    trips = make_trips(rng, stations, weather)

    PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    stations.drop(columns="_popularity").to_parquet(PARQUET_DIR / "stations.parquet", index=False)
    trips.to_parquet(PARQUET_DIR / "trips.parquet", index=False)
    weather.to_parquet(PARQUET_DIR / "daily_weather.parquet", index=False)

    con = duckdb.connect(str(DB_PATH))
    for t in ("stations", "trips", "daily_weather"):
        con.execute(f"CREATE OR REPLACE TABLE {t} AS SELECT * FROM read_parquet('{PARQUET_DIR / (t + '.parquet')}')")
    for t in ("stations", "trips", "daily_weather"):
        print(f"{t:14s} {con.execute(f'SELECT count(*) FROM {t}').fetchone()[0]:>9,} rows")
    con.execute("CHECKPOINT")  # flush WAL so the file opens cleanly read-only
    con.close()
    print(f"-> {DB_PATH}")


if __name__ == "__main__":
    build()
