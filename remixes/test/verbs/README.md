# `verbs` — MINI VERB and DEEP VERB

Both reverbs in one image, to compare them on a unit.

## What is in it

- **MINI VERB** — in DARK REV's FX2 row: a modulated diffused FDN. `modules/miniverb/README.md`.
- **DEEP VERB** — a new FX2 row: a true-stereo figure-eight of lossy allpasses. `modules/deepverb/README.md`.

Both are FX2 only and each instance takes the track's 16K FX2 buffer.

## Status

OCTABAM9, the author's unit, 12 Oct 2026, with DEEP VERB's previous voice (BAL, OFFST): both reverbs ran; DEEP VERB preferred (`modules/deepverb/README.md`, On the unit). The current voice (DIFF, RATE) is local only.

## Build

```bash
make image REMIX=verbs BUILD=1   # -> out/OCTATRACK_OCTABAM1.bin
```

[BUILDING.md](../../../docs/guide/BUILDING.md) is the walk-through from a fresh machine to a flashed unit. `make check REMIX=verbs` runs every gate first.
