# The stock reverbs: topology

What PLATE REV, SPRING REV and DARK REV are, as structures: read from the
disassembly of payload A (`tools/build/dsp_disasm_all.py` →
`out/dsp/payload_A.asm`) and the stock X tables (a `dsp_host` dump of the
stock image, `send_probe.dump_mem`). 13 Oct 2026, for comparing FIG8 VERB
(`modules/fig8verb/README.md`) with what the unit already has.

Markers: ✅ read directly (an instruction, a table word); 🟡 inferred from
the shape of the code, not traced end to end. Addresses are payload A's;
payload B's code and tables sit elsewhere (`DSP.md`, `TABLES.md`
"Payload-relative addresses"), and B was not read. Lengths are in samples
at the effect's own internal rate.

| effect | P range (A) | init / proc | words |
|---|---|---|---|
| PLATE REV | `P:0x01000..0x01251` | `0x01000` / `0x01055` | 594 |
| SPRING REV | `P:0x01252..0x01678` | `0x01252` / `0x012be` | 1,063 |
| DARK REV | `P:0x01679..0x01aa3` | `0x01679` / `0x0171b` | 1,067 |

All three are FX2-only and take the track's 16K allocator buffer
(`x:>$213` at init: PLATE `P:0x1018`, SPRING `P:0x1267`, DARK `P:0x1692`).
All three work in stages: one `do n7` pass over the block per stage, not one
per-sample loop.

## PLATE REV: a feedback delay network, full rate

| stage | what | |
|---|---|---|
| input | L+R summed to mono into a block (`P:0x10ab`) | ✅ |
| diffusion | 5 allpasses in series (`P:0x10bc`, `do #5`), lengths from `X:0x8585..0x8589` = 47, 67, 101, 149, 223; gains `X:0x8580..0x8584` = −0.963, +0.948, −0.922, +0.888, −0.837, each × (0.7 + a TIME term) × a fade-in value | ✅ |
| modulation | a 1,024-point sine (`X:0x6c00`, `TABLES.md`), its phase advanced by `n7` per block; a 6-tap interpolator (coefficients from `X:0x1cd9`) reads two modulated lines, modulo 2,048 and 1,024 (`P:0x1100`, `P:0x1122`) | ✅ |
| tank | 6 delay lines: the two modulated ones and four fixed, modulo `X:0x858a..0x858d` = 2,679, 2,463, 1,827, 1,637 (37–61 ms); each written with input/6 plus a mix of the others (`P:0x11ad`, `y0 = 1/6`) | ✅ |
| feedback mix | three pair-rotations (`P:0x1194`, `do #3`, the line pairs from `X:0x8590..0x859b`); per-line decay gains from a TIME-indexed table (`X:0x85a2 + 3·TIME/2`) | ✅ the rotations; 🟡 that together they form the full feedback matrix |
| output | a 6-coefficient stereo mix (`P:0x117b`) | ✅ |
| GATE | an envelope follower: `abs`, peak hold, a threshold from the GATE knob squared, attack and release ramps (`P:0x11c2..0x11f4`) | ✅ |
| filters | HP and LP one-poles from knob-indexed tables (`X:0x502 + HP`, `X:0x520 + LP`) | ✅ |
| mix | the shared output routine `func_001a47` (MIX, BAL, MIXF) | ✅ the call; 🟡 its role |

Knobs: TIME DAMP GATE HP LP MIX, GVOL BAL MONO MIXF.

## SPRING REV: three dispersive spring models, quarter rate

| stage | what | |
|---|---|---|
| rate | the block split in quarters (`n7` / 4, `P:0x12e3`); a polyphase FIR (`func_001586`, shared with DARK) decimates on the way in and interpolates on the way out | ✅ the quarter split and the FIR calls; 🟡 the factor of the FIR stages (11.025 kHz is inferred) |
| springs | three state blocks (`r7+$2d/$30/$33`), outputs at `$70`, `$74`, `$78` | ✅ |
| dispersion | per spring, a cascade of second-order allpasses: `do n4` with `n4 = $12` = 18 sections (`P:0x15a9`, `0x15d0`, `0x15fd`, `0x1638` unroll 1-4 sections a pass; one is picked from a count set at `P:0x1444`). This is the "chirp" of a real spring | ✅ the cascade; 🟡 the reading as dispersion |
| TYPE | picks the allpass coefficient table: `X:0x89a4`, `0x89ec` or `0x8a34` (`P:0x1426`) | ✅ |
| delays | three lines in 1K buffers, offsets 643, 733, 791 (`P:0x139c`): ~58, 66, 72 ms at quarter rate | ✅ the lengths; 🟡 the ms |
| cross-feed | a 3×3 mix of the three springs (`P:0x141a`), its gains from a TIME table (`X:0x8b70 + TIME`) | ✅ |
| filters | HP/LP from `X:0x8a7c`, `X:0x8afc` | ✅ |

Knobs: TIME HP LP MIX, TYPE (3) BAL. The dearest of the three: 257.5
instructions a sample (`modules/analog-bassdrum/CPU.md`; PLATE 183.9, DARK
193.2).

## DARK REV: a Schroeder network, half rate

| stage | what | |
|---|---|---|
| rate | the block split in halves (`n7` / 2, `P:0x1756`), the same FIR routine as SPRING both ways: nothing above ~11 kHz, which is the "dark" | ✅ the halving; 🟡 the cutoff |
| pre-delay | PRE (page-2 slot 6) × `$7f0` + 16 into a 2,048-word line (`P:0x17d4`): up to ~93 ms | ✅ |
| filters | a shelf (SHVG gain, SHVF frequency, `X:0x690 + SHVF`) and HP/LP (`X:0x502`, `X:0x520`) | ✅ |
| diffusion | 8 allpasses in series (`P:0x184e`, `do #8`), lengths from `X:0x8cfb..0x8d02` = 29, 37, 59, 83, 127, 191, 251, 409 (1.3–18.5 ms); gains `X:0x8cf3..0x8cfa` = +0.996 … −0.940 alternating, × a TIME factor from `X:0x8d0b + TIME` (0.625 … 0.715) | ✅ |
| modulation | the `X:0x6c00` sine, interpolated, moving a pair of allpass-like taps in a 2,048-word line (`P:0x18a3`) | ✅ |
| tank | three left/right pairs of damped feedback delays fed in parallel from the diffused signal (`func_001a3a`, `P:0x18c9..0x1924`), modulo 647/923, 609/1,377, 897/1,293 (28–62 ms) | ✅ the lengths and the pairing; 🟡 the feedback wiring of each pair |
| output | the pairs summed to L and R (`P:0x1936`), then a rotation/allpass stage (`func_001a25`, coefficients indexed by TIME from `X:0x290`) and the shared mix routine | ✅ the sum; 🟡 the last stage's role |

Knobs: TIME SHVG SHVF HP LP MIX, PRE BAL MONO MIXF.

## Side by side

| | PLATE | SPRING | DARK | MINI VERB | FIG8 VERB |
|---|---|---|---|---|---|
| family | FDN | physical spring model | Schroeder parallel combs | FDN (Householder) | figure-eight allpass loop |
| internal rate | 44.1 kHz | ~11 kHz | ~22 kHz | 44.1 kHz | 44.1 kHz |
| input | mono sum | mono | MONO knob | mono sum | true stereo |
| input diffusion | 5 allpasses | — (dispersion inside) | 8 allpasses | 4 allpasses | — (12 lossy allpasses in the loop) |
| tank | 6 delays, rotations | 3 delays, 18-section chirp each | 6 damped delays in pairs | 4 branches + matrix | 2 halves crossing sides |
| modulation | 2 lines, sine | — | 2 taps, sine | 2 allpasses, triangle | 4 taps, wander |
| pre-delay | — | — | ~93 ms | — | 61 ms |
| extras | GATE | TYPE | shelf | — | DIFF, WIDTH, RATE |

None of the stock reverbs is a figure-eight. DARK's half rate is how it
stays cheap: a tank run at half rate costs about half, for the top octave.
