# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

This repo holds two sibling implementations of the same app, "Tarifa Dinâmica": a ride-hailing price/travel-time
forecaster for **Uber or 99**, chosen in a selector. Prices are **simulated** — there is no free public Uber/99
pricing API, so `Oracle/` carries one fare table per company, simulates the market and the model learns that
oracle; the user closes the gap to the real app by typing an observed price, which refits that company's fare.
Addresses, routes, weather and location are real free services.

- `Desktop/` — the reference implementation: Python, CustomTkinter + matplotlib, dark mode only. Has its own
  `Desktop/CLAUDE.md` with full commands, architecture, invariants and repo-specific conventions — read it before
  touching anything under `Desktop/`.
- `Mobile/` — the Android port: Kotlin + Jetpack Compose. It reads the Desktop app's trained `data/model.json` and
  reproduces the oracle, model and engine bit-for-bit. See `Mobile/README.md` for build/toolchain commands and the
  parity-test structure (`ParityTest.kt`, `golden.py`).

There is no root-level build, lint or test — each subproject is self-contained (own venv / own Gradle wrapper).
Always `cd` into the relevant subproject and consult its own docs first.

## Cross-cutting invariant

The Android app is a line-by-line mirror of the Desktop app's `Oracle` (fare table, `getState`, `getSurge`,
`getPrice` and the `update` fare fit), `Model.get`, `Model.getQuantiles`, `Engine.get`, `Locator.getStart` and the
duration formatters. Any change to those on the Desktop side must be ported to
`Mobile/app/src/main/java/com/klauss/tarifa`, then verified with:

```bash
cd Mobile
../Desktop/venv/bin/python golden.py ../Desktop/data/model.json
./gradlew testDebugUnitTest
```

## Conventions

The global style guide (`~/Documents/Prompts/CODE_STYLE.md`) applies everywhere; `Desktop/CODE_STYLE.md` is the
copy checked into this repo. Beyond it, per subtree: Desktop code comments/logs are Portuguese without accents,
UI text is Portuguese with accents (see `Desktop/CLAUDE.md`); Mobile mirrors the same UI-language convention in
Kotlin.
