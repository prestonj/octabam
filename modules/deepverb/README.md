# `deepverb` — DEEP VERB

A true-stereo reverb for FX2: two cross-coupled chains of lossy allpasses,
each side's loop delay feeding the other. The tail moves between the sides
and a panned source keeps its place; the moving taps give it a deep,
chorused bloom. Not flashed.

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
| 1 | 3 | CHOR | 0–127, default 48 | depth of the two moving taps a side, 0..128 samples (2.9 ms) |
| 1 | 4 | PRE | 0–127, default 0 | pre-delay, 0..48 ms |
| 1 | 5 | WIDTH | 0–127, default 64 | wet mid/side: 0 mono, 64 as the tank makes it, 127 side ×1.5 |
| 2 | 6 | LCUT | 0–127, default 16 | loop high-pass, higher thins the tail |
| 2 | 8 | BAL | 0–127, default 96 | output tap: 0 after allpass 3 (grainier), 127 after allpass 6 |
| 2 | 10 | OFFST | −64..+63, default 0 | pre-delay offset: left side later below 0, right above, up to 15 ms |

Page 2 uses the even slots only: they are the ones the panel's page-2 knob
editor is proven to reach (`tools/remix/schema.py`, `Param`).

## Design

Per side, per sample:

    in /8 -> pre-delay (PRE, plus OFFSET on one side)
          + other side's loop-delay output * FB
          -> one-pole LP (DAMP) -> one-pole HP (LCUT: x - LP(x))
          -> six lossy allpasses, the first modulated
               EARLY tap after the third, LATE after the sixth
          -> loop delay, modulated -> the other side
    out = dry + MIX * (x4 * WIDTH(BAL blend of EARLY/LATE) - dry)

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

Samples at 44.1 kHz, primes. The two LFOs are triangles at 0.710 and
0.931 Hz; on each side the allpass tap and the loop tap move in opposite
directions. Taps read with linear interpolation.

`deepverb_ref.py` is the specification: the DSP's arithmetic in float
(integer tap positions with eight fraction bits, the same LFO phase, the
same gain staging). `gen_asm.py` writes `deepverb.asm` from it, so the
ring layout and constants cannot drift from the reference; edit the
generator, never the `.asm`.

## Smoothing

Knob words land once a call; nothing the audio touches jumps with them.

| what | how | a full 0 → 127 turn |
|---|---|---|
| MIX, BAL, WIDTH, and DECAY's absorb and cross-feed | a per-sample ramp toward the target, (target − now)/256 a sample, the step taken again each call: a 5.8 ms exponential in straight 16-sample pieces | ~95% in 17 ms |
| DAMP, LCUT (loop filter coefficients) | 1/16 of the way each call, landing exactly | ~95% in 17 ms |
| CHOR (the tap depth) | at most 1/32 of a sample of tap movement a call | 1.5 s, a glide of at most ~4 cents |
| PRE, OFFST (the pre-delay reads) | at most one sample a call | 0.77 s, the reverb's input sliding like tape |

The first call after the ring is clean snaps every value to its target, so
nothing glides up from zero at load.

Measured on a steady 440 Hz tone with each knob jumping 0 → 127 → 0 (the
peak 4th difference of the output, which holds the tone 96 dB down and
passes a step; LSB, the tone at 2.1M):

| knob | unsmoothed | smoothed |
|---|---:|---:|
| MIX | 7,229,264 | 20,356 |
| BAL | 18,576,546 | 52,998 |
| WIDTH | 2,125,781 | 9,687 |
| DECAY | 1,537,688 | 8,828 |
| CHOR | 884,831 | 4,951 |
| DAMP | 207,703 | 14,421 |
| LCUT | 26,656 | 5,888 (the steady tail reads 5,960) |
| PRE | 66,513 | 35,895 |
| OFFST | 172,432 | 37,501 |

What is left on MIX and BAL is the bend at the start of a fast fade, as any
per-sample one-pole has. **PRE and OFFST still slip a whole sample per
call while they move**; the tank diffuses it, but it is the one control
that is not clean (Open).

## Memory and ownership

One 16K Y ring at the instance's allocator buffer (FX2 only; an FX1 slot is
3,072 words), written at `r5` with `m5 = $3fff`. Segments, in order: the two
pre-delay lines (63.2 ms each, PRE + OFFSET), then each side's six
allpasses and loop delay, each with its moving excursion and one word of
margin: **16,114 of 16,384 words**. State lives in `x:(r7+$00..$4f)`; init
zeroes it all and preserves r1/n1/m1. The ring is cleared 128 words a call,
and the audio passes dry, bit-exact, until it is clean (128 calls, 46 ms).

## Measured (12 Oct 2026, `dsp_host`)

- **Cost: 22,536 instructions per core per block** with four instances a
  core: **111% of the per-track budget** (the stock spring's worst case,
  20,376, over its four tracks); **116%** with every knob moving and split
  calls (23,584). Over 100%: on a core running four DEEP VERBs, the other
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
- **Against the reference:** the residual is 73–88 dB under the
  reference's level at five settings (defaults, chorus at both decay
  extremes, every knob off-centre, partial MIX): 24-bit rounding.
- **Fixed point:** the LP and HP states keep 48 bits. With 24-bit states a
  tail settled on 596 LSB of DC (−83 dBFS) after a burst; with 48 bits the
  last second, 20 s on at DECAY 64, is within 24 LSB of zero (−111 dBFS).
  Rounding every ring store as well reaches exactly zero for another 5% of
  the budget; `gen_asm.py` has the table and the switch.
- **Decay** (float reference, impulse): DECAY 0 / 32 / 64 / 96 / 112 / 120 /
  127 → RT60 0.41 / 0.77 / 1.8 / 6.7 / 19 / 33 / 42 s. At 127 the length
  follows DAMP (49 s at DAMP 0, 21 s at DAMP 127).

## Open

- **PRE and OFFST slip a sample a call while they move.** An interpolated
  pre-delay read with a per-sample ramp would make them clean, for about
  28 instructions a sample (~9% of the budget).
- **Over budget** (111-116%). Left: dropping the absorb/feedback ramps (8
  a sample; DECAY jumps click again).
- **The `n5` stalls** (above): real cycles the instruction meter does not
  see. A burn measurement on the unit would price them.
- **The voicing is a first guess** (gains, LFO rates, filter ranges, the
  DECAY curve); listening will move it.
- **The near-freeze in fixed point:** a full-scale input at DECAY 127 is
  not gated for clipping inside the loop.
- **Not flashed.** Every number above is local.

## Gates

```sh
make verify-deepverb          # 29 gates: tools/verify/verify_deepverb.py
make check REMIX=deepverb
python3 modules/deepverb/gen_asm.py --check
python3 modules/deepverb/deepverb_ref.py IN.wav OUT.wav DECAY=110 CHORUS=100
```
