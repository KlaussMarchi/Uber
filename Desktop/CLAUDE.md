# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Desktop app in Python (CustomTkinter + matplotlib, dark mode only) that forecasts the price and travel time of a
ride-hailing trip over the next 12 h in 10-min steps, for **Uber or 99**, chosen in a selector. There is no free
public pricing API (Uber's estimate page requires login, its old endpoint is gone, 99 never had one), so prices are
**estimated from real data**: `market.py` collects the monthly averages Uber publishes per city pair
(`uber.com/global/pt-br/r/routes/…`, allowed by robots.txt and listed in the sitemap) and fits a no-surge fare, a price
level per origin municipality and the real travel pace over OSRM's free-flow time. The fitted table is
`Oracle/markets.json`; the raw data is `Oracle/routes.csv`. 99 is Uber × 0.90 (no public data). The user's observed
prices calibrate it (Bayesian level + decaying surge). Addresses, municipalities, routes, weather and location come
from real free services. `README.md` (Portuguese) documents the design and the measured results. `../Mobile` is the
Android port (Kotlin + Compose) that reads the same `markets.json` and reproduces the numbers bit for bit.

## Commands

Always run from the repo root with the venv (imports are root-relative, there is no package install).

```bash
source venv/bin/activate
python index.py         # app: database, worker thread, then the window; ready at once (no training)
python market.py        # refits Oracle/markets.json from Oracle/routes.csv (~20 s); without routes.csv it recrawls (~1 h)
python test.py          # full validation, ~20 min, needs network, opens a real Tk window, exit code 1 on failure
python -c "import test; test.testUtils()"    # one test function
```

- There is no test framework, linter or build. `test.py` uses its own `check(name, ok, detail)` that prints
  `[ok]`/`[FALHOU]` and accumulates `failures`; the `__main__` block points `database.path` to a temp folder, runs
  `worker.setup()` and calls the `test*` functions in order.
- `testUtils`, `testApi`, `testDistance` and `testMarket` stand alone; `testOracle`, `testModel` and the rest need
  `worker.setup()` first (it loads the table and the informed prices). `testWorker` needs `testRoutes` to have run:
  it only tracks routes with `used_at > 0`. `testModel` writes and deletes rows in `Fares`.
- `testMarket` refits the table from `routes.csv` and requires it to equal the committed `markets.json` (except
  `updated`): after changing anything the fit depends on, run `python market.py` and commit both.
- The tables in README "O que foi medido" are printed by `test.py` and `market.py`; update them when numbers move.
- `data/` holds only what the app writes (`surge.db`, `app.log`, alert WAVs). Delete it to start over.

## Architecture

Each component is a `Folder/index.py`. `api`, `database`, `engine` and `worker` end by instantiating themselves;
`Oracle`, `Model` and `Market` are modules of functions (no state besides `Oracle.MARKET` and `Oracle.FARES`). Tests
swap attributes on those instances instead of injecting dependencies (`database.path`, `engine.getWeather`,
`api.OSRM`, `Worker.index.time`), so keep them module-level singletons.

Flow of a quote, `Engine.getQuote` → `Engine.get`:

1. `Api` talks to Photon (autocomplete, reverse), Nominatim (free-text fallback and the origin municipality at
   `zoom=10`, one call per click/route), OSRM (route; the FOSSGIS mirror only when the demo server fails),
   Open-Meteo (rain, rain chance, timezone) and BeaconDB (location). Requests are spaced per host (`INTERVALS`) and
   **failures return `None`, never raise**.
2. `Engine.getRoute` caches the route, its IANA timezone and its origin municipality (`city`, `uf`) in `Routes` and
   stamps `used_at`. A route whose municipality lookup failed keeps `city = ''` and retries on the next use.
3. `Engine.get(route, company, now)` builds the grid: row 0 is the exact current instant, rows 1… are aligned
   10-min slots up to `HORIZON` (73 rows). It returns `None` when the (possibly stale) cached forecast no longer
   covers the horizon. `Model.get` returns `p10/p50/p90` and `m10/m50/m90`; `price`/`minutes`/`surge` are filled
   only on row 0 (`price` = `p50[0]`).
4. Failures reach the UI as `{'error': '<Portuguese message>'}`.

`Oracle` is the expected market. `load()` reads `markets.json` (called by `Engine.setup`). `getMarket(lat, lon, city,
uf)` returns the origin's log price level, log pace and level sigma: the municipality's own values when its
normalized key (`getKey`, no accents or case) is in the table, else a Gaussian-kernel mean of same-state
municipalities pulled to the state (`PRIOR`), else the national values. `getProfile` gives the hour-of-week
congestion — `TRAFFIC`, the real typical week of the TomTom Traffic Index 2025 averaged over 9 Brazilian metros,
interpolated between hour centres and exported as `week` in `markets.json` for the phone — and the demand (`PEAKS`,
Gaussian peaks accumulated one by one so the phone sums in the same order). `getState` returns the
demand excess, the congestion ratio and the minutes: OSRM duration × pace (`getPace`: fitted slopes on km and OSRM
speed, clipped to `PACE`), modulated by the profile around `MARKET['traffic']` with `FREE` of the average delay left
at dawn, plus rain. `getTariff(level, distance, minutes, excess, company)` = exp(level) × company ratio ×
max(floor, base + km·d + minute·m + long·max(d − `LONG`, 0)) × surge. `getCalibration` turns the informed prices
(`FARES`, last `SAMPLE` per company) into a posterior level (`mean`, `var`) and the latest observation's surge
(`last`, `ts`, `weight`), weighting each price by the distance of its origin (`RADIUS`) and bounding typos
(`LIMITS`). `getAnchor` is the neutral calibration used to store the uncalibrated central price with each informed
price.

`Model.getBands` mixes a dry and a wet scenario per instant with the Open-Meteo chance (`getScenarios`: the wet
intensity is the forecast divided by the chance), each a lognormal whose sigma combines the calibration variance, the
dynamic spread (`DYNAMIC`, `DYNAMIC_PEAK`) and the travel-time spread through the per-minute share; the informed
price's surge holds for `HOLD` and decays with `TAU`, and at that instant the whole spread vanishes so the informed
price shows exactly. Quantiles come from `getQuantile` (bisection on the mixture CDF, `getNormal` = Numerical Recipes
erfc so both apps compute the same bits). `get` rounds; `getBands` does not (used for the stored expected price).

`Market` is the research pipeline behind `market.py`: `getRoutes` (sitemap sample, page parse every `DELAY` s, IBGE
seats, OSRM seat to seat), `getPaceFit`/`getPriceFit` (ridge-shrunk city → state → national levels, alternating with
the fare shape by least squares; the national level is folded into the fare so it is in reais and `FLOOR` holds
nationally), `getValidation` (10-fold by route and by city) and `getTable` (the JSON, with sigmas set so the 80%
band covers 80% of out-of-fold errors). `getWeek` weights the week by trip volume: the fit matches Uber's monthly
average, not an instant.

`Worker` is a daemon thread aligned to 10-min slots: each slot it stores one 12 h forecast per company for the 5 most
recently used routes in `Forecasts`, and `check` joins `Fares` to the forecasts made before each informed price for
the same route, company and slot to measure the real error and band coverage (`Metrics`, status bar after `SAMPLE`
matched prices). `index.py` runs `worker.setup()` (database + engine) before the window, so the table is loaded when
the UI starts. Any exception in a cycle is logged and the next slot retries.

`Interface` (window, `Chart/`, `Search/`, `Locator/`): **no network or model call on the Tk thread**. Every async
result carries the company it was computed for (`showTick(route, company, df)`), and `showForecast` drops a result
whose company is no longer the selected one. Alert baselines and levels are keyed by `(route_id, company)`. Work goes
through `Interface.submit(work, cb)` (a `ThreadPoolExecutor`); the callback returns through a queue polled every
50 ms with `after`, and only callbacks touch widgets. The side panel is a `CTkScrollableFrame` whose scrollbar is
painted only when the content does not fit (`showScroll`); the header status and the chart heading get a wraplength
from `<Configure>`. `Locator.getStart` picks where the map opens. `Engine.getPlaces` skips routes with `used_at = 0`.

Time: every `ts` is Unix seconds. Demand and traffic follow the route's local clock via
`Utils.functions.getClock(ts, tz)`, where national holidays count as Sunday (weekday 6).

## Invariants to preserve

- Changing a table schema → bump `Database.VERSION` and extend `Database.migrate` (and the phone's `onUpgrade`):
  derived tables (`Forecasts`, `Metrics`) are dropped, user routes (`used_at > 0`) and informed prices (`Fares` in the
  current format) are kept.
- Anything the fit depends on (`TRAFFIC`, `PEAKS`, `DEMAND_SURGE`, `FREE`, `VOLUME`, `LONG`, `KM_REF`, `SPEED_REF`,
  `PACE`, the fare formula) → run `python market.py`, then `testMarket` must pass, then regenerate the phone's golden
  data.
- The Android app mirrors `Oracle` (all of it), `Model.getNormal`, `getQuantile`, `getScenarios`, `getBands`, `get`,
  `Engine.get`/`getGrid`/`setFare`, `Locator.getStart` and the duration formatters line by line, in the same
  operation order. After changing any of them, port the change to `../Mobile/app/src/main/java/com/klauss/tarifa`,
  run `venv/bin/python ../Mobile/golden.py` and `./gradlew testDebugUnitTest` in `../Mobile`.
- The informed price shows exactly at its instant: `setFare` stores `expected` = the anchored `getBands` median and
  the level used; `getCalibration` rescales old prices to the current level. Keep both sides of that contract.
- `p10 < p50 < p90` and `m10 < m50 < m90` at every point (`MIN_BAND`); more rain or more rain chance never lowers a
  quantile; travel time is identical across companies; 99 < Uber at every instant.
- Zero cost: only free, open services — no commercial APIs, no keys, no Docker, everything local. The Uber pages are
  read only by `market.py`, slowly (`DELAY`), never by the app at runtime.

## Conventions specific to this repo

The global style guide applies; `CODE_STYLE.md` at the repo root is the copy to follow. Beyond it: UI text and
`worker.status` are Portuguese with accents; code comments and log messages are Portuguese **without** accents;
physical and service limits live as `UPPER_SNAKE_CASE` constants with a short lowercase comment carrying the unit or
reason.
