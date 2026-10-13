# `verbs` — MINI VERB and FIG8 VERB

Both reverbs in one image, to compare them on a unit.

## What is in it

- **MINI VERB** — in DARK REV's FX2 row: a modulated diffused FDN. `modules/miniverb/README.md`.
- **FIG8 VERB** — a new FX2 row: a true-stereo figure-eight of lossy allpasses. `modules/fig8verb/README.md`.

Both are FX2 only and each instance takes the track's 16K FX2 buffer.

## Status

OCTABAM9 and OCTABAM11, the author's unit, 12-13 Oct 2026: both reverbs ran; FIG8 VERB preferred. OCTABAM11 (the current voice) carried four FIG8 VERBs and four FILTERs on tracks 1-4 without a hang (`modules/fig8verb/README.md`, On the unit).

## Build

```bash
make image REMIX=verbs BUILD=1   # -> out/OCTATRACK_OCTABAM1.bin
```

[BUILDING.md](../../../docs/guide/BUILDING.md) is the walk-through from a fresh machine to a flashed unit. `make check REMIX=verbs` runs every gate first.
