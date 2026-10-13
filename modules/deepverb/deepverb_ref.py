#!/usr/bin/env python3
"""Float reference for DEEPVERB: a true-stereo figure-eight allpass loop.

The topology is the published one (Dattorro, "Effect Design Part 1", JAES
45(9), 1997, figure 1's tank; Griesinger's cross-coupled loop). The delay
lengths, gains, LFOs, control laws and filter forms are this module's own,
sized to fit one 16,384-word FX2 instance buffer.

Each side of the loop, per sample:

    in -> pre-delay (PRE, plus OFFSET on one side) -> + other side's loop-delay output * FB
       -> one-pole LP (DAMP) -> one-pole HP (LOWCUT, x - LP)
       -> N lossy allpasses (the first modulated, linear interpolation)
             tap EARLY after allpass N/2, LATE after allpass N
       -> loop delay (modulated, linear interpolation) -> the other side

A lossy allpass scales its delayed sample by ABSORB before the lattice:
    s = absorb * d;  w = x + g*s;  y = s - g*w;  write w.

Every operation is one the DSP sample loop can do in the form it will be
written: no per-sample division or transcendental; the coefficients are
computed per 16-sample block, the LFOs are per-sample triangles.

    python3 modules/deepverb/deepverb_ref.py IN.wav OUT.wav [--aps 8] [knob=value ...]
"""
import argparse
import array
import math
import sys
import wave

FS = 44100
BLOCK = 16

# Allpass lengths per side, the first modulated (samples at 44.1 kHz, primes).
# The voice runs the first six (6 and 8 sounded alike, 12 Oct 2026);
# the last two are kept for comparison renders (--aps 8).
AP_L = (293, 797, 593, 337, 499, 719, 613, 853)
AP_R = (359, 773, 641, 353, 467, 701, 587, 919)
LOOP_L, LOOP_R = 1867, 1733
AP_G = (0.65, 0.62, 0.62, 0.60, 0.60, 0.60, 0.60, 0.60)

DEPTH_MAX = 128          # samples, peak-to-peak excursion of a moving tap
PRE_MAX_MS = 48.0        # both sides
OFF_MAX_MS = 15.0        # OFFSET delays one side by up to this, on top of PRE
                         # 6 allpasses: each pre-delay line 63 ms, 16,093 of 16,384 words
LFO_HZ = (0.71, 0.93)    # left, right

# Knobs, 0..127 as the panel sends them.
# OFFSET: 64 none, below delays the left side, above the right (+-15 ms).
# WIDTH: wet mid/side, 0 mono, 64 as the tank makes it, 127 side x1.5
# (x2 clipped the wet output at full-scale input, 12 Oct 2026).
DEFAULTS = dict(DECAY=96, DAMP=64, LOWCUT=16, CHORUS=48, BAL=96, MIX=127, PRE=0, OFFSET=64,
                WIDTH=64)


def laws(k):
    """Panel knobs -> per-block coefficients. Every law is linear or a short
    table on the unit; kept that simple here."""
    # Squared in (1 - DECAY). At 127 the allpasses are lossless and the
    # cross-feed 0.99: only the loop filters remove energy, a near-freeze.
    u = (1 - k['DECAY'] / 127) ** 2
    c = dict(
        absorb=1.0 - 0.15 * u,           # in every allpass (Q23 max on the DSP)
        fb=0.99 - 0.49 * u,              # cross-feed, at most 0.99
        # one-pole LP coefficient: 0.95 (bright) .. 0.15 (dark)
        lp=0.95 - 0.80 * k['DAMP'] / 127,
        # one-pole HP (x - LP of x): 0.002 (~14 Hz) .. 0.10 (~740 Hz)
        hp=0.002 + 0.098 * (k['LOWCUT'] / 127) ** 2,
        depth=DEPTH_MAX * k['CHORUS'] / 127,
        late=k['BAL'] / 127,             # LATE share; EARLY gets the rest
        mix=k['MIX'] / 127,
        # side gain: 0..1 below the centre, 1..1.5 above it
        width=(k['WIDTH'] / 64 if k['WIDTH'] <= 64 else 1 + 0.5 * (k['WIDTH'] - 64) / 63),
        pre=int(round(PRE_MAX_MS * FS / 1000 * k['PRE'] / 127)),
        off=int(round(OFF_MAX_MS * FS / 1000 * max(-63, k['OFFSET'] - 64) / 63)),
    )
    return c


class Ring:
    """One delay line: write at the head, read back d samples (d >= 1)."""
    __slots__ = ('buf', 'mask', 'head')

    def __init__(self, longest):
        size = 1
        while size < longest + 4:
            size <<= 1
        self.buf = [0.0] * size
        self.mask = size - 1
        self.head = 0


def memory_words(aps):
    """Ring positions the DSP layout needs (one 16K ring, segments as in
    Mini Verb): each line's longest read plus the write, moving taps with
    their excursion and the interpolation neighbour, both pre-delays."""
    line = int((PRE_MAX_MS + OFF_MAX_MS) * FS / 1000)
    half = DEPTH_MAX // 2
    total = 2 * (line + 1)
    for ap, loop in ((AP_L[:aps], LOOP_L), (AP_R[:aps], LOOP_R)):
        total += ap[0] + half + 2                 # AP1 swings +-half
        total += sum(n + 1 for n in ap[1:])
        total += loop + DEPTH_MAX + 2             # loop delay swings 0..DEPTH
    return total


def render(left, right, knobs, aps=6):
    """left/right: lists of floats in -1..1. Returns (out_l, out_r)."""
    k = dict(DEFAULTS, **knobs)
    c = laws(k)
    n = len(left)
    pre_max = int((PRE_MAX_MS + OFF_MAX_MS) * FS / 1000) + 2
    side = []
    for ap, loop, hz in ((AP_L[:aps], LOOP_L, LFO_HZ[0]), (AP_R[:aps], LOOP_R, LFO_HZ[1])):
        side.append(dict(
            pre=Ring(pre_max),
            aps=[Ring(m + DEPTH_MAX) for m in ap], lens=ap,
            loop=Ring(loop + DEPTH_MAX), loop_len=loop,
            lp=0.0, hp=0.0, out=0.0,
            phase=0.0, step=4 * hz / FS,        # triangle, -1..1, period 1/hz
            early=0.0, late=0.0))
    gains = AP_G[:aps]
    mid = aps // 2
    out_l, out_r = [0.0] * n, [0.0] * n
    absorb, fb, lp, hp = c['absorb'], c['fb'], c['lp'], c['hp']
    depth, late, mix, width = c['depth'], c['late'], c['mix'], c['width']
    pre = (c['pre'] + max(0, -c['off']), c['pre'] + max(0, c['off']))

    for i in range(n):
        dry = (left[i], right[i])
        fed = (side[1]['out'], side[0]['out'])     # the figure-eight crossing
        for s, S in enumerate(side):
            # triangle LFO in -1..1
            ph = S['phase'] + S['step']
            if ph > 1.0:
                ph = 2.0 - ph
                S['step'] = -S['step']
            elif ph < -1.0:
                ph = -2.0 - ph
                S['step'] = -S['step']
            S['phase'] = ph

            r = S['pre']
            r.buf[r.head] = dry[s]
            x = r.buf[(r.head - pre[s]) & r.mask]
            r.head = (r.head + 1) & r.mask

            x += fed[s] * fb
            S['lp'] += lp * (x - S['lp'])
            x = S['lp']
            S['hp'] += hp * (x - S['hp'])
            x = x - S['hp']

            for j, (r, m, g) in enumerate(zip(S['aps'], S['lens'], gains)):
                if j == 0:
                    t = m + 0.5 * depth * ph
                    ti = int(t)
                    fr = t - ti
                    a = r.buf[(r.head - ti) & r.mask]
                    b = r.buf[(r.head - ti - 1) & r.mask]
                    dl = a + fr * (b - a)
                else:
                    dl = r.buf[(r.head - m) & r.mask]
                sv = absorb * dl
                w = x + g * sv
                r.buf[r.head] = w
                r.head = (r.head + 1) & r.mask
                x = sv - g * w
                if j == mid - 1:
                    S['early'] = x
            S['late'] = x

            r = S['loop']
            r.buf[r.head] = x
            t = S['loop_len'] + 0.5 * depth * (1.0 - ph)   # 0..depth, opposite AP1
            ti = int(t)
            fr = t - ti
            a = r.buf[(r.head - ti) & r.mask]
            b = r.buf[(r.head - ti - 1) & r.mask]
            S['out'] = a + fr * (b - a)
            r.head = (r.head + 1) & r.mask

        wl = late * side[0]['late'] + (1.0 - late) * side[0]['early']
        wr = late * side[1]['late'] + (1.0 - late) * side[1]['early']
        m, sd = 0.5 * (wl + wr), 0.5 * (wl - wr) * width
        out_l[i] = dry[0] + mix * (m + sd - dry[0])
        out_r[i] = dry[1] + mix * (m - sd - dry[1])
    return out_l, out_r


def read_wav(path, seconds=None):
    with wave.open(str(path), 'rb') as w:
        ch, width, fs = w.getnchannels(), w.getsampwidth(), w.getframerate()
        frames = w.getnframes() if seconds is None else min(w.getnframes(), int(seconds * fs))
        raw = w.readframes(frames)
    if fs != FS:
        sys.exit(f'{path}: {fs} Hz, need {FS}')
    if width == 2:
        data = array.array('h', raw)
        scale = 32768.0
    elif width == 3:
        data = [int.from_bytes(raw[k:k + 3], 'little', signed=True) for k in range(0, len(raw), 3)]
        scale = 8388608.0
    else:
        sys.exit(f'{path}: {8 * width}-bit')
    if ch == 1:
        mono = [v / scale for v in data]
        return mono, list(mono)
    return [v / scale for v in data[0::2]], [v / scale for v in data[1::2]]


def write_wav(path, left, right):
    out = bytearray()
    for l, r in zip(left, right):
        for v in (l, r):
            q = max(-8388608, min(8388607, int(round(v * 8388607))))
            out += q.to_bytes(3, 'little', signed=True)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(2)
        w.setsampwidth(3)
        w.setframerate(FS)
        w.writeframes(bytes(out))


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('inp')
    p.add_argument('out')
    p.add_argument('knobs', nargs='*', help='NAME=0..127')
    p.add_argument('--aps', type=int, default=6, choices=(4, 6, 8))
    p.add_argument('--seconds', type=float, default=None)
    p.add_argument('--tail', type=float, default=0.0, help='seconds of silence appended')
    p.add_argument('--gain', type=float, default=0.5, help='input gain (headroom)')
    a = p.parse_args()
    knobs = {kv.split('=')[0].upper(): int(kv.split('=')[1]) for kv in a.knobs}
    for name in knobs:
        if name not in DEFAULTS:
            sys.exit(f'unknown knob {name}; knobs: {", ".join(DEFAULTS)}')
    left, right = read_wav(a.inp, a.seconds)
    pad = [0.0] * int(a.tail * FS)
    left = [v * a.gain for v in left] + pad
    right = [v * a.gain for v in right] + pad
    out_l, out_r = render(left, right, knobs, a.aps)
    write_wav(a.out, out_l, out_r)
    print(f'{a.out}: {len(out_l) / FS:.2f} s, aps={a.aps}, '
          f'memory {memory_words(a.aps)} of 16384 words')


if __name__ == '__main__':
    main()
