# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

This repo holds two sibling implementations of the same app, "Tarifa Dinâmica": a ride-hailing price/travel-time
forecaster for **Uber or 99**, chosen in a selector. There is no free public Uber/99 pricing API, so prices are
**estimated from real data**: the Desktop's `market.py` collects the monthly averages Uber publishes per city pair and
fits a no-surge fare, a price level per origin municipality and the real travel pace over OSRM (`Oracle/markets.json`,
data in `Oracle/routes.csv`); 99 is Uber × 0.90. The user's observed prices calibrate it (Bayesian level plus a
decaying surge). Addresses, municipalities, routes, weather and location are real free services.

- `Desktop/` — the reference implementation: Python, CustomTkinter + matplotlib, dark mode only. Has its own
  `Desktop/CLAUDE.md` with full commands, architecture, invariants and repo-specific conventions — read it before
  touching anything under `Desktop/`.
- `Mobile/` — the Android port: Kotlin + Jetpack Compose. It reads the Desktop's `Oracle/markets.json` (copied to its
  assets) and reproduces the market, the calibration, the bands and the engine bit-for-bit. See `Mobile/README.md`
  for build/toolchain commands and the parity-test structure (`ParityTest.kt`, `golden.py`).

There is no root-level build, lint or test — each subproject is self-contained (own venv / own Gradle wrapper).
Always `cd` into the relevant subproject and consult its own docs first.

## Cross-cutting invariant

The Android app is a line-by-line mirror of the Desktop app's `Oracle` (all of it: `getProfile`, `getMarket`,
`getPace`, `getState`, `getFree`, `getSurge`, `getTariff`, `getShare`, `update`, `getCalibration`, `getAnchor`),
`Model` (`getNormal`, `getQuantile`, `getScenarios`, `getBands`, `get`), `Engine.get`/`getGrid`/`setFare`,
`Locator.getStart`, the duration formatters and the database migration. Any change to those on the Desktop side, or to
`markets.json`, must be ported to `Mobile/app/src/main/java/com/klauss/tarifa`, then verified with:

```bash
cd Mobile
../Desktop/venv/bin/python golden.py
./gradlew testDebugUnitTest
```

## Conventions

The global style guide (`~/Documents/Prompts/CODE_STYLE.md`) applies everywhere; `Desktop/CODE_STYLE.md` is the
copy checked into this repo. Beyond it, per subtree: Desktop code comments/logs are Portuguese without accents,
UI text is Portuguese with accents (see `Desktop/CLAUDE.md`); Mobile mirrors the same UI-language convention in
Kotlin.
