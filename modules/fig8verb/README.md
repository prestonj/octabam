# `fig8verb` — FIG8 VERB

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
| 2 | 10 | RATE | 0–127, default 20 | wander speed: the faster of each side's two triangles, 0.10..4.0 Hz on the left, 1.31× on the right; 20 is 0.71 Hz |

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

Samples at 44.1 kHz, primes. Taps read with linear interpolation; on each
side the allpass tap and the loop tap move in opposite directions.

**The moving taps wander.** Each side's offset follows
`depth · (|A| + |B|) / 2`, A and B triangles at a ratio of 1.618 that are
never in step, so the sum drifts rather than swings like a vibrato. It is
sampled once a full call, 16 samples ahead, and walked to in a straight
line a sample at a time (`e += de`). The right side's triangles run 1.31×
the left's.

`fig8verb_ref.py` is the specification: the DSP's arithmetic in float
(integer tap positions with eight fraction bits, the same LFO phase, the
same gain staging). `gen_asm.py` writes `fig8verb.asm` from it, so the
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
| RATE | none needed: a new step keeps the wander's phases continuous | immediate |

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

## Calls

The dispatcher calls twice in a block after a trig (the first sub-block with
`a = 0`, the rest with `a ≠ 0`) and once otherwise. Every per-block job runs
on the `a ≠ 0` call only: from one of those to the next is 16 samples
whatever the split, so the ramps and the wander are stepped for 16 samples
and the first sub-block carries the same straight pieces on. (A version
that divided by the call's own frame count overshot on a split of 15: the
1-frame call's step ran 15 more samples on the next block and read outside
the ring. The eight-instance gate caught it; 12 Oct 2026.) Each knob's law
runs only when its masked word changes: 5 instructions a knob otherwise.

## Memory and ownership

One 16K Y ring at the instance's allocator buffer (FX2 only; an FX1 slot is
3,072 words), written at `r5` with `m5 = $3fff`. Segments, in order: the two
pre-delay lines (61.2 ms each), then each side's six allpasses and loop
delay, each with its moving excursion and four words of margin: **16,328
of 16,384 words**. State lives in `x:(r7+$00..$62)`; init zeroes it (the
last-knob-word slots to −1, so every law runs once) and preserves
r1/n1/m1. The ring is cleared 128 words a call, and the audio
passes dry, bit-exact, until it is clean (128 calls, 46 ms).

## On the unit

Named DEEP VERB (`DEEP`) until 13 Oct 2026, and on both flashes below; the
same effect and the same FX2 id, 0x1b, so saved projects keep it.

OCTABAM9 (remix `verbs`, beside Mini Verb), the author's unit, 12 Oct 2026:
the voice before this one (BAL and OFFST, CHOR to 128 samples, PRE to
48 ms). Heard: preferred to Mini Verb, the high-pass the reason; more
shimmer than Mini Verb; the true-stereo input not obvious on the material
played. BAL was audible only at its ends and OFFST not at all once a tail
had formed (both move only the onset); CHOR was subtle and PRE short. This
voice answers those: OFFST's memory went to CHOR (twice the depth) and PRE
(61 ms), BAL became DIFF with its grainy tap two allpasses earlier, and
RATE took OFFST's slot.

OCTABAM11 (remix `verbs`), the author's unit, 13 Oct 2026: the current
voice (the wander, four a core at 99.3% of the budget by instruction
count). Four FIG8 VERBs on FX2 and four FILTERs on FX1 of tracks 1-4,
all eight effects on one core: the unit did not hang and it sounded
good. Not measured: a burn reading of the spare cycles, or a capture
checked for overload artifacts.

## Measured (12 Oct 2026, `dsp_host`)

- **Cost: 20,228 instructions per core per block** with four instances a
  core and split calls: **99.3% of the per-track budget** (the stock
  spring's worst case, 20,376, over its four tracks). Four FIG8 VERBs fit a
  core within stock spring's cost.

  | case | per core per block | budget |
  |---|---:|---:|
  | knobs still, no splits | 20,152 | 98.9% |
  | knobs still, split calls | 20,228 | 99.3% |
  | one knob moving every block (an LFO), split calls | 20,316 | 99.7% |
  | all nine knobs moving every block, split calls | 20,832 | 102.2% |

  How it got there (split calls, knobs still unless said):

  | | loop | budget |
  |---|---:|---:|
  | first DSP version, no smoothing | 349 | 113% (unsplit) |
  | smoothing (per-sample ramps) | 369 | 126% (knobs moving) |
  | allpasses: the gain in parallel, the output in the other accumulator; the fraction from `a0` | 337 | 115% |
  | the output as one 2×2 mix; no `rnd` in the 48-bit states | 316 | 109% |
  | the wander LFO, stepped once a call | 306 | 109% |
  | per-block work on the `a ≠ 0` call only | 306 | 103% |
  | the LP state back to 24 bits; the knob helper inline | 302 | 101% |
  | each knob's law only when its word changes | 302 | 99.3% |

  The parallel forms and operand pairs are ones stock already runs (`mpy
  x0,x0,a #>$7fffff,y1`, `move a0,b`, `lsr b`, `move b1,y0`, `mpy x1,y0`,
  `mac y1,x1`); stock has no `y:(Rn+Nn)` in a parallel move, so every ring
  access stays a plain move. Every `n5` and `r4` is loaded two
  instructions or more before its use, so the ring accesses should not
  stall (Mini Verb's do; stock writes an index register and uses it at
  once in 1 place of 13,068). These are executed instructions; the cycle
  count on the chip is inferred close to them, not measured.
- **Against the reference:** the residual is 78–96 dB under the
  reference's level at five settings (defaults, chorus at both decay
  extremes, every knob off-centre, partial MIX): 24-bit rounding.
- **Fixed point:** the HP state keeps 48 bits. With 24-bit states a tail
  settled on 596 LSB of DC (−83 dBFS) after a burst; with the HP's 48 bits
  the last second, 20 s on at DECAY 64, is within 40 LSB of zero
  (−106 dBFS). A 48-bit LP state and `rnd` around the updates changed it
  by 0-24 LSB and were taken out; rounding every ring store reaches exactly
  zero for ~5% of the budget. `gen_asm.py` has the table and the switches.
- **Decay** (float reference, impulse, the voice before CHOR doubled):
  DECAY 0 / 32 / 64 / 96 / 112 / 120 / 127 → RT60 0.41 / 0.77 / 1.8 / 6.7 /
  19 / 33 / 42 s. At 127 the length follows DAMP (49 s at DAMP 0, 21 s at
  DAMP 127).

## Open

- **PRE slips a sample a call while it moves.** An interpolated pre-delay
  read with a per-sample ramp would make it clean, for about 28
  instructions a sample (~9% of the budget). Pre-delay past 61 ms needs a
  mono line (~120 ms) or 12-bit storage (~120 ms stereo).
- **Four a core is measured in instructions, not cycles.** A burn
  measurement on the unit would confirm it. With every knob moving every
  block it is 102%; dropping the absorb/feedback ramps (8 a sample) would
  buy that back at the price of clicks on DECAY jumps.
- **The voicing is a first guess** (gains, LFO ranges, filter ranges, the
  DECAY curve); listening will move it.
- **The near-freeze in fixed point:** a full-scale input at DECAY 127 is
  not gated for clipping inside the loop.

## Gates

```sh
make verify-fig8verb          # 29 gates: tools/verify/verify_fig8verb.py
make check REMIX=fig8verb
python3 modules/fig8verb/gen_asm.py --check
python3 modules/fig8verb/fig8verb_ref.py IN.wav OUT.wav DECAY=110 CHORUS=100 RATE=60
```
