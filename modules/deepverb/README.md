# `deepverb` — DEEP VERB

A true-stereo reverb for FX2: two cross-coupled chains of lossy allpasses,
each side's loop delay feeding the other. A panned source keeps its side
in the tail; the moving taps give it a chorused shimmer, and the loop
high-pass keeps a long tail clean.

Its topology is the published figure-eight tank (J. Dattorro, *Effect
Design Part 1*, JAES 45(9), 1997, figure 1; Griesinger's cross-coupled
loop). The delay lengths, gains, LFOs, control laws and filter forms are
this module's own, sized for one 16,384-word FX2 instance buffer.

## Knobs

| page | slot | name | range | what it does |
|---|---|---|---|---|
| 1 | 0 | DECAY | 0–127, default 96 | RT60 0.4 s .. 42 s; 127 is a near-freeze |
| 1 | 1 | DAMP | 0–127, default 64 | loop low-pass, higher is darker |
| 1 | 2 | MIX | 0–127, default 40 | dry/wet; 127 is fully wet |
| 1 | 3 | CHOR | 0–127, default 48 | depth of the two moving taps a side, 0..256 samples (5.8 ms) |
| 1 | 4 | PRE | 0–127, default 0 | pre-delay, 0..61 ms |
| 1 | 5 | WIDTH | 0–127, default 64 | wet mid/side: 0 mono, 64 as the tank makes it, 127 side ×1.5 |
| 2 | 6 | LCUT | 0–127, default 16 | loop high-pass, higher thins the tail |
| 2 | 8 | DIFF | 0–127, default 96 | diffusion: 0 grainy (the tap after allpass 2), 127 smooth (after allpass 6) |
| 2 | 10 | RATE | 0–127, default 20 | chorus LFO rate, 0.10..4.0 Hz on the left, 1.31× that on the right; 20 is 0.71 Hz |

Page 2 uses the even slots only: they are the ones the panel's page-2 knob
editor is proven to reach (`tools/remix/schema.py`, `Param`). WIDTH 64, not
0, is the setting to compare with a reverb that has no width control: 0 is
a mono wet signal by design.

## Design

Per side, per sample:

    in /8 -> pre-delay (PRE)
          + other side's loop-delay output * FB
          -> one-pole LP (DAMP) -> one-pole HP (LCUT: x - LP(x))
          -> six lossy allpasses, the first modulated
               EARLY tap after the second, LATE after the sixth
          -> loop delay, modulated -> the other side
    out = dry + MIX * (x4 * WIDTH(DIFF blend of EARLY/LATE) - dry)

A lossy allpass scales its delayed sample before the lattice:
`s = absorb·d; w = x − g·s; y = s + g·w`. DECAY sets `absorb` (0.85 → 1.0)
and the cross-feed `fb` (0.50 → 0.99), both squared in (1 − DECAY), so the
top of the knob is where only the loop filters remove energy.

| line | left | right |
|---|---:|---:|
| allpass 1 (moving ±depth/2), g 0.65 | 293 | 359 |
| allpass 2, 3, g 0.62 | 797, 593 | 773, 641 |
| allpass 4, 5, 6, g 0.60 | 337, 499, 719 | 353, 467, 701 |
| loop delay (moving 0..depth) | 1867 | 1733 |

Samples at 44.1 kHz, primes. The two LFOs are triangles; on each side the
allpass tap and the loop tap move in opposite directions. Taps read with
linear interpolation.

`deepverb_ref.py` is the specification: the DSP's arithmetic in float
(integer tap positions with eight fraction bits, the same LFO phase, the
same gain staging). `gen_asm.py` writes `deepverb.asm` from it, so the
ring layout and constants cannot drift from the reference; edit the
generator, never the `.asm`.

## Smoothing

Knob words land once a call; nothing the audio touches jumps with them.

| what | how | a full 0 → 127 turn |
|---|---|---|
| MIX, DIFF, WIDTH, and DECAY's absorb and cross-feed | a per-sample ramp toward the target, (target − now)/256 a sample, the step taken again each call: a 5.8 ms exponential in straight 16-sample pieces | ~95% in 17 ms |
| DAMP, LCUT (loop filter coefficients) | 1/16 of the way each call, landing exactly | ~95% in 17 ms |
| CHOR (the tap depth) | at most 1/16 of a sample of tap movement a call | 1.5 s, a glide of at most ~7 cents |
| PRE (the pre-delay reads) | at most one sample a call | 0.98 s, the reverb's input sliding like tape |
| RATE | none needed: a new step keeps the LFO phase continuous | immediate |

The first call after the ring is clean snaps every value to its target, so
nothing glides up from zero at load.

Measured on a steady 440 Hz tone with each knob jumping 0 → 127 → 0 (the
peak 4th difference of the output, which holds the tone 96 dB down and
passes a step; LSB, the tone at 2.1M). "Unsmoothed" is the first DSP
version, with BAL where DIFF is (its early tap after allpass 3):

| knob | unsmoothed | smoothed |
|---|---:|---:|
| MIX | 7,229,264 | 9,551 |
| DIFF | 18,576,546 (BAL) | 28,771 |
| WIDTH | 2,125,781 | 4,145 |
| DECAY | 1,537,688 | 1,887 |
| CHOR | 884,831 | 9,854 |
| DAMP | 207,703 | 5,788 |
| LCUT | 26,656 | 6,838 |
| PRE | 66,513 | 40,348 |
| RATE | — | 2,829 |

What is left on MIX and DIFF is the bend at the start of a fast fade, as
any per-sample one-pole has. **PRE still slips a whole sample per call
while it moves**; the tank diffuses it, but it is the one control that is
not clean (Open).

## Memory and ownership

One 16K Y ring at the instance's allocator buffer (FX2 only; an FX1 slot is
3,072 words), written at `r5` with `m5 = $3fff`. Segments, in order: the two
pre-delay lines (61.2 ms each), then each side's six allpasses and loop
delay, each with its moving excursion and one word of margin: **16,320 of
16,384 words**. State lives in `x:(r7+$00..$4f)`; init zeroes it all and
preserves r1/n1/m1. The ring is cleared 128 words a call, and the audio
passes dry, bit-exact, until it is clean (128 calls, 46 ms).

## On the unit

OCTABAM9 (remix `verbs`, beside Mini Verb), the author's unit, 12 Oct 2026:
the voice before this one (BAL and OFFST, CHOR to 128 samples, PRE to
48 ms). Heard: preferred to Mini Verb, the high-pass the reason; more
shimmer than Mini Verb; the true-stereo input not obvious on the material
played. BAL was audible only at its ends and OFFST not at all once a tail
had formed (both move only the onset); CHOR was subtle and PRE short. This
voice answers those: OFFST's memory went to CHOR (twice the depth) and PRE
(61 ms), BAL became DIFF with its grainy tap two allpasses earlier, and
RATE took OFFST's slot. **This voice is not flashed yet.**

## Measured (12 Oct 2026, `dsp_host`)

- **Cost: 22,516 instructions per core per block** with four instances a
  core: **111% of the per-track budget** (the stock spring's worst case,
  20,376, over its four tracks); **116%** with every knob moving and split
  calls (23,528). Over 100%: on a core running four DEEP VERBs, the other
  effects on those tracks pay the 11-16%. The sample loop is 337
  instructions.

  | | loop | unsplit | worst |
  |---|---:|---:|---:|
  | first DSP version, no smoothing | 349 | 113% | — |
  | smoothing (per-sample ramps) | 369 | 121% | 126% |
  | allpasses with the gain in parallel, output in the other accumulator; the fraction from `a0`; no phase reload | 337 | 111% | 116% |

  The parallel forms are ones stock already runs (`mpy x0,x0,a #>$7fffff,y1`,
  `move a0,b`, `lsr b`, `move b1,y0`); stock has no `y:(Rn+Nn)` in a
  parallel move, so every ring access stays a plain move. These are
  executed instructions, not hardware cycles: like Mini Verb, every ring
  access uses `n5` straight after loading it, which the 56300 interlocks
  with a stall the meter does not count (stock does it once in 13,068
  instructions). The cycle cost on the chip is higher than these numbers;
  by how much is unmeasured.
- **Against the reference:** the residual is 78–98 dB under the
  reference's level at five settings (defaults, chorus at both decay
  extremes, every knob off-centre, partial MIX): 24-bit rounding.
- **Fixed point:** the LP and HP states keep 48 bits. With 24-bit states a
  tail settled on 596 LSB of DC (−83 dBFS) after a burst; with 48 bits the
  last second, 20 s on at DECAY 64, is within 24 LSB of zero (−111 dBFS).
  Rounding every ring store as well reaches exactly zero for another 5% of
  the budget; `gen_asm.py` has the table and the switch.
- **Decay** (float reference, impulse, the voice before CHOR doubled):
  DECAY 0 / 32 / 64 / 96 / 112 / 120 / 127 → RT60 0.41 / 0.77 / 1.8 / 6.7 /
  19 / 33 / 42 s. At 127 the length follows DAMP (49 s at DAMP 0, 21 s at
  DAMP 127).

## Open

- **PRE slips a sample a call while it moves.** An interpolated pre-delay
  read with a per-sample ramp would make it clean, for about 28
  instructions a sample (~9% of the budget). Pre-delay past 61 ms needs a
  mono line (~120 ms) or 12-bit storage (~120 ms stereo).
- **Over budget** (111-116%). Left: dropping the absorb/feedback ramps (8
  a sample; DECAY jumps click again).
- **The `n5` stalls** (above): real cycles the instruction meter does not
  see. A burn measurement on the unit would price them.
- **The voicing is a first guess** (gains, LFO ranges, filter ranges, the
  DECAY curve); listening will move it.
- **The near-freeze in fixed point:** a full-scale input at DECAY 127 is
  not gated for clipping inside the loop.

## Gates

```sh
make verify-deepverb          # 29 gates: tools/verify/verify_deepverb.py
make check REMIX=deepverb
python3 modules/deepverb/gen_asm.py --check
python3 modules/deepverb/deepverb_ref.py IN.wav OUT.wav DECAY=110 CHORUS=100 RATE=60
```
