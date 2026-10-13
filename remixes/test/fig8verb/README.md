# `fig8verb` — FIG8 VERB alone

The development image for FIG8 VERB's gates.

## What is in it

- **FIG8 VERB** — a true-stereo figure-eight allpass reverb on FX2 (id 0x1b, a new chooser row; DARK REV stays): six lossy allpasses a side, the loop delays crossing sides, deep chorus with a rate, diffusion, width. `modules/fig8verb/README.md`.

## Status

Local render only (`make verify-fig8verb`: the float reference, dry while clearing, LFO-dirty knob words, eight instances isolated, a decaying tail). Not flashed.

## Build

```bash
make image REMIX=fig8verb BUILD=1   # -> out/OCTATRACK_OCTABAM1.bin
```

[BUILDING.md](../../../docs/guide/BUILDING.md) is the walk-through from a fresh machine to a flashed unit. `make check REMIX=fig8verb` runs every gate first.
