# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Desktop app in Python (CustomTkinter + matplotlib, dark mode only) that forecasts the price and travel time of a
ride-hailing trip over the next 12 h in 10-min steps. Prices are **synthetic**: `Oracle/` simulates the market and
the model learns the oracle. Addresses, routes, weather and location come from real free services. `prompt.md` is
the original specification; `README.md` (Portuguese) documents the design choices and the measured results.
`../Mobile` is the Android port (Kotlin + Compose) that reads this app's `data/model.json` and reproduces the oracle,
model and engine bit for bit.

## Commands

Always run from the repo root with the venv (imports are root-relative, there is no package install).

```bash
source venv/bin/activate
python index.py         # app: starts the worker thread, then the window
python bootstrap.py     # only generates the synthetic history into data/surge.db, no window
python test.py          # full validation, ~12 min, needs network, opens a real Tk window, exit code 1 on failure
python -c "import test; test.testUtils()"    # one test function
```

- There is no test framework, linter or build. `test.py` uses its own `check(name, ok, detail)` that prints
  `[ok]`/`[FALHOU]` and accumulates `failures`; the `__main__` block calls the `test*` functions in order.
- Only `testUtils`, `testApi` and `testDistance` stand alone. `testBootstrap` points `database.path` and
  `model.path` to a temp folder and returns the routes the later tests receive; calling `testOracle`,
  `testScenarios`, `testPrecision`, `testGeneralization`, `testShock`, `testRoutes`, `testWorker` or `testInterface`
  without it uses the real `data/`.
- The tables in README "O que foi medido" are printed by `test.py`; update them when a change moves the numbers.
- `data/` holds everything generated (`surge.db`, `model.json`, `app.log`, alert WAVs). Delete it to start over.

## Architecture

Each component is a `Folder/index.py` that ends by instantiating itself (`api`, `database`, `engine`, `model`,
`worker`); everyone imports the instance. Tests swap attributes on those instances instead of injecting
dependencies (`database.path`, `engine.getWeather`, `api.OSRM`, `Worker.index.time`), so keep them module-level
singletons.

Flow of a quote, `Engine.getQuote` → `Engine.get`:

1. `Api` talks to Photon (autocomplete, reverse), Nominatim (free-text fallback, one call per click — its policy
   forbids autocomplete), OSRM (route + fraction on the RJ-106 corridor; the FOSSGIS mirror is only tried when the
   public demo server fails), Open-Meteo (rain and rain chance, archive, timezone — the archive has no chance, so it
   is only requested from the forecast) and BeaconDB (location). Requests are spaced per host (`INTERVALS`) and
   **failures return `None`, never raise**.
2. `Engine.getRoute` caches the route and its IANA timezone in `Routes` and stamps `used_at`.
3. `Engine.get` builds the grid: row 0 is the exact current instant, rows 1… are aligned 10-min slots up to
   `HORIZON` (73 rows). It returns `None` when the (possibly stale) cached forecast no longer covers the horizon.
   The "observed now" price and time come from `Oracle.getMarket`; today's earlier observations come from
   `Prices`. `Model.get` returns `p10/p50/p90` and `m10/m50/m90`; `price`/`minutes` are filled only on row 0.
4. Failures reach the UI as `{'error': '<Portuguese message>'}`.

`Oracle.getMarket(route, ts, rain)` is the single source of "truth": `bootstrap.py` uses it for a year of history
(real rain + injected storms, 14 real routes from 4 to 186 km and 0 to 97 % on the corridor), `Worker` for the
observation every slot, `Engine` for the current price. It is deterministic by seed per local day; a local day
whose midnight does not exist (DST) starts at the first existing instant (`nonexistent='shift_forward'`).

`Model` predicts a **multiplier** over a base (`getTariff` without surge for price, free-flow duration for time),
because trees do not extrapolate distance. Three layers: LightGBM quantile boosters per target × quantile; an
anchor regression per lead on today's residuals (`beta`, 4 columns: intercept, current residual clipped at
`CLIP` within-day deviations, the excess over the clip — a shock such as an accident — and the shrunk mean of the
clipped residuals of the day); conformal offsets per lead × regime (`REGIMES` = how many observations today).
Calibration days are drawn in `BLOCK`-day blocks so leads that cross midnight stay inside the calibration set.
Quantiles are rearranged over `RAIN_GRID` so more rain never lowers them. The whole state is swapped under
`lock` and written atomically to `model.json`.

`Worker` is a daemon thread aligned to 10-min slots: on the first boot it bootstraps and trains (~50 s for the
736 k rows); then each slot it observes the 5 most recently used routes (`used_at > 0`) into `Prices`, stores their
forecasts in `Forecasts`, consolidates past forecasts into `Metrics` and retrains every `RETRAIN` s. The UI only
reads `worker.status`, which shows the band hit rate only after `SAMPLE` consolidated forecasts. Any exception in a
cycle is logged and the next slot retries.

`Interface` (window, `Chart/`, `Search/`, `Locator/`): **no network or model call on the Tk thread**. Work goes
through `Interface.submit(work, cb)` (a `ThreadPoolExecutor`); the callback returns through a queue polled every
50 ms with `after`, and only callbacks touch widgets. Child widgets reach it by `winfo_toplevel().submit` or
`master.submit`. The chart receives the full 12 h series and only crops the visible window; the last panel is the
travel time in minutes/hours (`getDuration`, `getDelay`, `getSpan`). `Locator.getStart` picks where the map opens:
a precise estimate, the most recent used place inside a coarse estimate's error, the last used place, or the region.
`Engine.getPlaces` skips routes with `used_at = 0`, which is how `bootstrap.py` marks its synthetic routes.

Time: every `ts` is Unix seconds. Demand and traffic follow the route's local clock via
`Utils.functions.getClock(ts, tz)`, where national holidays count as Sunday (weekday 6).

## Invariants to preserve

- Changing the `Prices`/`Routes` schema, the oracle or the bootstrap routes → bump `Database.VERSION`; the DB is
  dropped and the worker regenerates history and retrains. Changing model features (`Model.process`) → also delete
  `data/model.json`, since `Model.setup` only rejects a saved model without `clip` or whose `beta`/`offsets` shapes
  differ.
- The Android app mirrors `Oracle`, `Model.get`, `Model.getQuantiles`, `Engine.get`, `Locator.getStart` and the
  duration formatters line by line. After changing any of them, port the change to `../Mobile/app/src/main/java/com/klauss/tarifa`, run
  `venv/bin/python ../Mobile/golden.py data/model.json` and `./gradlew testDebugUnitTest` in `../Mobile`.
- Oracle calibration: the reference route (`ORIGIN` → `DESTINATION` in `Utils/variables.py`) must keep a median
  weekday 9–16 h dry price between R$ 54,67 and R$ 55,00, with rush + heavy rain peaks near R$ 90; `test.py`
  asserts both.
- The bootstrap routes must keep long routes off the corridor and short routes on it; with the 5 original routes
  (every long route ~62 % on the corridor) the model confused distance with corridor and `testGeneralization`
  fails (time bias +2 %, band coverage 60 %).
- `p10 < p50 < p90` and `m10 < m50 < m90` at every point (`MIN_BAND`).
- Zero cost: only free, open services — no commercial APIs, no Docker, everything local under `data/`.

## Conventions specific to this repo

The global style guide applies; its canonical path is missing on this machine, so `CODE_STYLE.md` at the repo
root is the copy to follow. Beyond it: UI text and `worker.status` are Portuguese with accents; code comments
and log messages are Portuguese **without** accents; physical and service limits live as `UPPER_SNAKE_CASE`
class constants with a short lowercase comment carrying the unit or reason.
