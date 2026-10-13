#!/usr/bin/env python3
"""Float reference for DEEPVERB: a true-stereo figure-eight allpass loop.

The topology is the published one (Dattorro, "Effect Design Part 1", JAES
45(9), 1997, figure 1's tank; Griesinger's cross-coupled loop). The delay
lengths, gains, LFOs, control laws and filter forms are this module's own,
sized to fit one 16,384-word FX2 instance buffer.

Each side of the loop, per sample:

    in -> pre-delay (PRE) -> + other side's loop-delay output * FB
       -> one-pole LP (DAMP) -> one-pole HP (LOWCUT, x - LP)
       -> N lossy allpasses (the first modulated, linear interpolation)
             tap EARLY after allpass 2, LATE after allpass N (DIFF blends them)
       -> loop delay (modulated, linear interpolation) -> the other side

A lossy allpass scales its delayed sample by ABSORB before the lattice:
    s = absorb * d;  w = x - g*s;  y = s + g*w;  write w.

Every operation is one the DSP sample loop can do in the form it will be
written: no per-sample division or transcendental; the coefficients are
computed per 16-sample block, the LFOs are per-sample triangles. This file is
the specification deepverb.asm is gated against: the arithmetic below is
the DSP's (integer tap positions, LFO phase, gain staging), in float.

    python3 modules/deepverb/deepverb_ref.py IN.wav OUT.wav [knob=value ...]
"""
import argparse
import array
import math
import sys
import wave

FS = 44100
BLOCK = 16
RING = 16384             # the FX2 allocator buffer, one modulo ring

# Allpass lengths per side, the first modulated (samples at 44.1 kHz, primes).
# The voice runs the first six (6 and 8 sounded alike, 12 Oct 2026);
# the last two are kept for comparison renders (--aps 8, float only).
AP_L = (293, 797, 593, 337, 499, 719, 613, 853)
AP_R = (359, 773, 641, 353, 467, 701, 587, 919)
LOOP_L, LOOP_R = 1867, 1733
AP_G = (0.65, 0.62, 0.62, 0.60, 0.60, 0.60, 0.60, 0.60)
APS = 6

DEPTH_MAX = 256          # samples, peak-to-peak excursion of a moving tap
PRE_K = 2700             # PRE at 127: 61.2 ms, what the ring has left (layout())
PRE_LINE = PRE_K         # longest pre-delay read
EARLY_AP = 2             # DIFF's grainy end: the tap after this allpass
# RATE: the LFO phase step per sample (of 2^24), q = RATE/127:
#   left  38 + q*1484  (0.10 .. 4.0 Hz), right 50 + q*1944 (1.31x the left)
RATE_L, RATE_R = (38, 1484), (50, 1944)
IN_SHIFT, OUT_SHIFT = 3, 2   # tank input /8, wet output x4: -18 dB headroom
MIX_SHIFT = 3                # the output mix's coefficients are held /8

# Knobs, 0..127 as the panel sends them.
# WIDTH: wet mid/side, 0 mono, 64 as the tank makes it, 127 side x1.5
# (x2 clipped the wet output at full-scale input, 12 Oct 2026).
# RATE 20: 0.71 Hz left, 0.93 right (the rates before RATE was a knob).
DEFAULTS = dict(DECAY=96, DAMP=64, LOWCUT=16, CHORUS=48, DIFF=96, MIX=127, PRE=0, RATE=20,
                WIDTH=64)
Q23 = 1 << 23


def q23(v):
    """A coefficient as the DSP holds it."""
    return min(round(v * Q23), Q23 - 1)


def knob_q(k):
    """k/127 as deepverb.asm computes it: w + w*(1/127), w = k << 16,
    saturated at the largest Q23 value."""
    w = k << 16
    return min(w + ((w * 0x010204) >> 23), Q23 - 1)


def layout(aps=APS):
    """Segment starts in the one ring (positions behind the write head):
    pre L, pre R, then per side AP1..APn and the loop delay. Each segment
    holds its longest read: AP1 m+64 and the loop L+128 (the moving taps),
    one word of margin past each moving read."""
    seg, at = {}, 0
    for name in ('preL', 'preR'):
        seg[name] = at
        at += PRE_LINE + 1
    for side, ap, loop in (('L', AP_L[:aps], LOOP_L), ('R', AP_R[:aps], LOOP_R)):
        for j, m in enumerate(ap):
            seg[f'ap{j + 1}{side}'] = at
            at += m + (DEPTH_MAX // 2 + 2 if j == 0 else 1)
        seg[f'loop{side}'] = at
        at += loop + DEPTH_MAX + 2
    seg['end'] = at
    return seg


def memory_words(aps=APS):
    return layout(aps)['end']


def laws(k):
    """Panel knobs -> per-block values, in the DSP's integer forms."""
    q = {name: knob_q(v) / Q23 for name, v in k.items()}
    # Squared in (1 - DECAY). At 127 the allpasses are lossless and the
    # cross-feed 0.99: only the loop filters remove energy, a near-freeze.
    u = (1 - q['DECAY']) ** 2
    qc = knob_q(k['CHORUS'])
    kw = k['WIDTH'] << 16
    wq = kw / Q23 if k['WIDTH'] <= 64 else 0.5 + (kw / Q23 - 0.5) * (0x410410 / Q23)
    pre = (knob_q(k['PRE']) * PRE_K) >> 23
    qr = knob_q(k['RATE'])
    return dict(
        absorb=1.0 - 0.15 * u,           # in every allpass
        fb=0.99 - 0.49 * u,              # cross-feed, at most 0.99
        # one-pole LP coefficient: 0.95 (bright) .. 0.15 (dark)
        lp=0.95 - 0.80 * q['DAMP'],
        # one-pole HP (x - LP of x): 0.002 (~14 Hz) .. 0.10 (~740 Hz)
        hp=0.002 + 0.098 * q['LOWCUT'] ** 2,
        dk=qc >> 7,                      # depth * 256, 0..65535
        half=qc >> 8,                    # depth/2 * 256
        late=q['DIFF'],                  # LATE share; EARLY gets the rest
        step=(RATE_L[0] + ((qr * RATE_L[1]) >> 23), RATE_R[0] + ((qr * RATE_R[1]) >> 23)),
        # The output is one 2x2 mix: L = A*wl + B*wr + D*dryL, R = A*wr + B*wl + D*dryR,
        # every coefficient held /8. MIX, WIDTH (side gain 2*wq) and the x4 makeup
        # fold into A and B: x4 * mix * ((1 +- side) / 2) / 8.
        cA=q['MIX'] * (0.25 + wq / 2),
        cB=q['MIX'] * (0.25 - wq / 2),
        cD=(1 - q['MIX']) / 8,
        pre=(pre, pre),
    )


def _tap(buf, head, t256):
    """Linear interpolation at t256/256 samples behind head, as the DSP
    reads it: d0 = ceil, f = the fraction below it."""
    d0 = -((-t256) // 256)
    f = ((-t256) % 256) / 256
    a = buf[(head - d0) % RING]
    return a + f * (buf[(head - d0 + 1) % RING] - a)


def render(left, right, knobs, aps=APS):
    """left/right: lists of floats in -1..1. Returns (out_l, out_r).
    The arithmetic is the DSP's in float: one ring, integer tap positions
    with 8 fraction bits, the same LFO phase and the same gain staging."""
    k = dict(DEFAULTS, **knobs)
    c = laws(k)
    seg = layout(aps)
    if seg['end'] > RING:
        sys.exit(f'{aps} allpasses need {seg["end"]} words, the ring is {RING}')
    ring = [0.0] * RING
    n = len(left)
    gains = AP_G[:aps]
    sides = []
    for side, ap, loop, step in (('L', AP_L[:aps], LOOP_L, c['step'][0]),
                                 ('R', AP_R[:aps], LOOP_R, c['step'][1])):
        sides.append(dict(name=side, ap=ap, loop=loop, step=step, phase=0,
                          lp=0.0, hp=0.0, out=0.0, early=0.0, late=0.0))
    out_l, out_r = [0.0] * n, [0.0] * n
    absorb, fb, lp, hp = c['absorb'], c['fb'], c['lp'], c['hp']
    dk, half, late = c['dk'], c['half'], c['late']
    cA, cB, cD = c['cA'], c['cB'], c['cD']
    gin, gmix = 1 / (1 << IN_SHIFT), 1 << MIX_SHIFT
    head = 0

    for i in range(n):
        dry = (left[i], right[i])
        fed = (sides[1]['out'], sides[0]['out'])     # the figure-eight crossing
        for s, S in enumerate(sides):
            old = S['phase'] - (1 << 24) if S['phase'] & 0x800000 else S['phase']
            u = old + S['step']                   # unwrapped, as the accumulator holds it
            S['phase'] = u & 0xFFFFFF
            p = min(abs(u), Q23 - 1)              # past +1 it saturates: p is still about 1
            e256 = (p * dk) >> 23
            sn = S['name']

            w0 = head - seg['pre' + sn]
            ring[w0 % RING] = dry[s] * gin
            x = ring[(w0 - c['pre'][s]) % RING]

            x += fed[s] * fb
            S['lp'] += lp * (x - S['lp'])
            x = S['lp']
            S['hp'] += hp * (x - S['hp'])
            x = x - S['hp']

            for j, (m, g) in enumerate(zip(S['ap'], gains)):
                w0 = head - seg[f'ap{j + 1}{sn}']
                if j == 0:
                    dl = _tap(ring, w0, m * 256 - half + e256)
                else:
                    dl = ring[(w0 - m) % RING]
                sv = absorb * dl
                w = x - g * sv
                ring[w0 % RING] = w
                x = sv + g * w
                if j == EARLY_AP - 1:
                    S['early'] = x
            S['late'] = x

            w0 = head - seg['loop' + sn]
            ring[w0 % RING] = x
            S['out'] = _tap(ring, w0, S['loop'] * 256 + dk - e256)
        head += 1

        wl = sides[0]['early'] + late * (sides[0]['late'] - sides[0]['early'])
        wr = sides[1]['early'] + late * (sides[1]['late'] - sides[1]['early'])
        out_l[i] = max(-1.0, min(1.0, gmix * (cA * wl + cB * wr + cD * dry[0])))
        out_r[i] = max(-1.0, min(1.0, gmix * (cA * wr + cB * wl + cD * dry[1])))
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
    p.add_argument('--aps', type=int, default=APS, choices=(4, 6))
    p.add_argument('--seconds', type=float, default=None)
    p.add_argument('--tail', type=float, default=0.0, help='seconds of silence appended')
    p.add_argument('--gain', type=float, default=1.0, help='input gain')
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
