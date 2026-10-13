# `deepverb` — DEEP VERB alone

The development image for DEEP VERB's gates.

## What is in it

- **DEEP VERB** — a true-stereo figure-eight allpass reverb on FX2 (id 0x1b, a new chooser row; DARK REV stays): six lossy allpasses a side, the loop delays crossing sides, deep chorus, pre-delay with a stereo offset, width. `modules/deepverb/README.md`.

## Status

Local render only (`make verify-deepverb`: the float reference, dry while clearing, LFO-dirty knob words, eight instances isolated, a decaying tail). Not flashed.

## Build

```bash
make image REMIX=deepverb BUILD=1   # -> out/OCTATRACK_OCTABAM1.bin
```

[BUILDING.md](../../../docs/guide/BUILDING.md) is the walk-through from a fresh machine to a flashed unit. `make check REMIX=deepverb` runs every gate first.
