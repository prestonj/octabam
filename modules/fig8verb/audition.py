#!/usr/bin/env python3
"""Render a WAV through the assembled FIG8 VERB (dsp_host, one instance).

The DSP code, not the float reference: the image is built from the
`fig8verb` test remix (out/mainos_bus.bin is put back afterwards), and the
input plays after the ring clear and a short warm-up.

    python3 modules/fig8verb/audition.py IN.wav OUT.wav [KNOB=value ...] [--tail S]

Knobs by their panel names (DECAY DAMP MIX CHOR PRE WIDTH LCUT DIFF RATE),
0..127; the rest stay at their defaults. 44.1 kHz WAVs, 16- or 24-bit.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/verify'))
import verify_fig8verb as v  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('inp')
    p.add_argument('out')
    p.add_argument('knobs', nargs='*', help='NAME=0..127, panel names')
    p.add_argument('--tail', type=float, default=3.0, help='seconds of silence after the input')
    a = p.parse_args()
    knobs = {}
    for kv in a.knobs:
        name, value = kv.split('=')
        name = name.upper()
        if name not in v.NAMES:
            sys.exit(f'unknown knob {name}; knobs: {" ".join(n for n in v.NAMES if n)}')
        knobs[name] = int(value)
    left, right = v.ref.read_wav(a.inp)
    pad = [0.0] * int(a.tail * v.ref.FS)
    left, right = left + pad, right + pad
    mems = v.build_standalone()
    (track,), _ = v.run(mems, 'audition', [(left, right)], [knobs])
    out_l, out_r = (c[v.WARM * v.FR:][:len(left)] / 8388607.0 for c in track)
    v.ref.write_wav(a.out, list(out_l), list(out_r))
    print(f'{a.out}: {len(left) / v.ref.FS:.2f} s, {knobs or "defaults"}')


if __name__ == '__main__':
    main()
