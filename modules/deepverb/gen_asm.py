#!/usr/bin/env python3
"""Write modules/deepverb/deepverb.asm from deepverb_ref.py's layout and laws.

The ring offsets, gains and control-law constants are computed here from
the reference, so the DSP source and the float specification cannot drift
apart; tools/verify/verify_deepverb.py refuses a deepverb.asm that differs
from this script's output.

    python3 modules/deepverb/gen_asm.py          # rewrite deepverb.asm
    python3 modules/deepverb/gen_asm.py --check  # exit 1 if it would change
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import deepverb_ref as ref  # noqa: E402

ASM = HERE / 'deepverb.asm'
# Fixed-point hygiene, measured 12 Oct 2026: a half-second noise burst, then
# 20 s of silence at DECAY 64, the last second's residue (one instance):
#   neither                        596 LSB of DC (-83 dBFS)  5,485 instr/block
#   HP state 48 bits                15 LSB                   5,613
#   LP and HP states 48 bits         9 LSB (-119 dBFS)       5,741   <- this
#   and every ring store rounded     0                       5,997
# The HP coefficient is as small as 0.002, so a truncated 24-bit state
# sticks, and the loop circulates what it holds.
RND = False
LP48 = True
HP48 = True
MASK = 0xFFFFFF

# x:(r7+slot), the instance block. Init zeroes $00..$2f.
SLOT = dict(
    base=0x00, head=0x01, clear=0x02,
    absorb=0x04, fb=0x05, lpc=0x06, hpc=0x07, dk=0x08,
    b1L=0x09, b1R=0x0a, b9L=0x0b, b9R=0x0c, prnL=0x0d, prnR=0x0e,
    mix=0x0f, bal=0x10, wid=0x11, pre=0x12,
    phL=0x14, phR=0x15, eL=0x16, eR=0x17,
    lpL=0x18, hpL=0x19, lpR=0x1a, hpR=0x1b, outL=0x1c, outR=0x1d, noutL=0x1e,
    lpLlo=0x24, hpLlo=0x25, lpRlo=0x26, hpRlo=0x27,
    earlyL=0x20, earlyR=0x21, lateL=0x22, lateR=0x23,
)
ZERO_WORDS = 0x30


def h(v):
    """A 24-bit immediate."""
    return f'${v & MASK:06x}'


def x(slot):
    return f'x:(r7+${SLOT[slot]:02x})'


def q(v):
    return h(ref.q23(v))


def generate():
    seg = ref.layout()
    if seg['end'] > ref.RING:
        raise SystemExit(f'layout needs {seg["end"]} words, the ring is {ref.RING}')
    g = [ref.q23(v) for v in ref.AP_G[:ref.APS]]
    out = []
    emit = out.append

    def ins(op, args='', note=''):
        line = f'        {op:<8}{args}'
        emit(f'{line:<40}; {note}' if note else line)

    def interp(note):
        """a = P (positive, 8 fraction bits) -> a = the interpolated read."""
        ins('move', 'a,b', note)
        ins('asr', '#$8,a,a')
        ins('move', 'a1,n5', 'the integer part: ring offset of the older word')
        ins('lua', '(r5+n5),r4')
        ins('and', '#>$ff,b', 'b is non-negative: b2 stays zero')
        ins('asl', '#$f,b,b')
        ins('move', 'b,y0', 'f, the fraction toward the newer word')
        ins('move', 'y:(r4)+,b')
        ins('move', 'y:(r4),a')
        ins('sub', 'b,a')
        ins('move', 'a,x0')
        ins('mpy', 'y0,x0,a')
        ins('add', 'b,a')

    def allpass(j, side, start, m):
        """Lossy allpass j (1-based) with a = x; y0 = absorb on entry."""
        gw = h(g[j - 1])
        if j > 1:
            ins('move', f'#>{h(-(start + m))},n5', f'AP{j}{side} {m}, g={ref.AP_G[j - 1]}')
            ins('move', 'y:(r5+n5),x0')
        ins('mpy', 'y0,x0,b', 's = absorb * d')
        ins('move', 'b,x0')
        ins('move', f'#>{gw},y1')
        ins('mac', '-x0,y1,a', 'w = x - g*s')
        ins('move', f'#>{h(-start)},n5')
        if RND:
            ins('rnd', 'a')
        ins('move', 'a,y:(r5+n5)')
        ins('move', 'a,x0')
        ins('move', 'b,a')
        ins('mac', 'x0,y1,a', 'y = s + g*w')

    def side(s):
        o = 'R' if s == 'L' else 'L'
        ap = ref.AP_L if s == 'L' else ref.AP_R
        loop = ref.LOOP_L if s == 'L' else ref.LOOP_R
        emit(f'; ---- side {s} ----')
        ins('move', 'x:(r0),a' if s == 'L' else 'x:(r0+n0),a', f'{s} in')
        ins('asr', f'#${ref.IN_SHIFT:x},a,a', 'tank headroom')
        ins('move', f'#>{h(-seg["pre" + s])},n5')
        if RND:
            ins('rnd', 'a')
        ins('move', 'a,y:(r5+n5)')
        ins('move', f'{x("prn" + s)},n5', 'pre-delay read, per block')
        ins('move', 'y:(r5+n5),a')
        ins('move', f'{x("out" + o)},x0', f'the figure-eight: {o} loop output, last sample')
        ins('move', f'{x("fb")},y1')
        ins('mac', 'x0,y1,a')
        ins('move', f'{x("lp" + s)},b', 'one-pole LP, DAMP')
        if LP48:
            ins('move', f'{x("lp" + s + "lo")},b0', 'its low word: the state keeps 48 bits')
        ins('sub', 'b,a')
        if LP48:
            ins('rnd', 'a')
        ins('move', 'a,x0')
        ins('move', f'{x("lpc")},y1')
        ins('mpy', 'x0,y1,a')
        ins('add', 'b,a')
        ins('move', f'a,{x("lp" + s)}')
        if LP48:
            ins('move', f'a0,{x("lp" + s + "lo")}')
            ins('rnd', 'a')
        ins('move', 'a,x1', 'one-pole HP, LOWCUT: x - LP(x)')
        ins('move', f'{x("hp" + s)},b')
        if HP48:
            ins('move', f'{x("hp" + s + "lo")},b0', 'its low word: the state keeps 48 bits')
        ins('sub', 'b,a')
        if HP48:
            ins('rnd', 'a')
        ins('move', 'a,x0')
        ins('move', f'{x("hpc")},y1')
        ins('mpy', 'x0,y1,a')
        ins('add', 'b,a')
        ins('move', f'a,{x("hp" + s)}')
        if HP48:
            ins('move', f'a0,{x("hp" + s + "lo")}')
            ins('rnd', 'a')
        ins('move', 'a,x0')
        ins('move', 'x1,a')
        ins('sub', 'x0,a')
        start = seg['ap1' + s]
        ins('move', 'a,x1', f'park x; AP1{s} {ap[0]} +- depth/2, g={ref.AP_G[0]}')
        ins('move', f'{x("b1" + s)},a')
        ins('move', f'{x("e" + s)},x0')
        ins('sub', 'x0,a', 'P = B1 - e')
        interp('the moving read')
        ins('move', 'a,x0')
        ins('move', f'{x("absorb")},y0', 'absorb, held in y0 through the chain')
        ins('mpy', 'y0,x0,b', 's = absorb * d')
        ins('move', 'b,x0')
        ins('move', 'x1,a')
        ins('move', f'#>{h(g[0])},y1')
        ins('mac', '-x0,y1,a', 'w = x - g*s')
        ins('move', f'#>{h(-start)},n5')
        if RND:
            ins('rnd', 'a')
        ins('move', 'a,y:(r5+n5)')
        ins('move', 'a,x0')
        ins('move', 'b,a')
        ins('mac', 'x0,y1,a', 'y = s + g*w')
        for j in range(2, ref.APS + 1):
            allpass(j, s, seg[f'ap{j}{s}'], ap[j - 1])
            if j == ref.APS // 2:
                ins('move', f'a,{x("early" + s)}', 'EARLY tap')
        ins('move', f'a,{x("late" + s)}', 'LATE tap')
        ins('move', f'#>{h(-seg["loop" + s])},n5', f'loop delay {loop} + depth - e')
        if RND:
            ins('rnd', 'a')
        ins('move', 'a,y:(r5+n5)')
        ins('move', f'{x("b9" + s)},a')
        ins('move', f'{x("e" + s)},x0')
        ins('add', 'x0,a', 'P = B9 + e')
        interp('the moving read')
        ins('move', f'a,{x("nout" + s if s == "L" else "outR")}',
            'R reads the old L output this sample' if s == 'L' else 'L has read it')

    def lfo(s, step):
        ins('move', f'{x("ph" + s)},a', f'LFO {s}: phase += {step}, p = |phase|')
        ins('move', f'#>{step},x0')
        ins('add', 'x0,a')
        ins('move', f'a1,{x("ph" + s)}', 'wraps: a1 is stored raw')
        ins('move', f'{x("ph" + s)},a')
        ins('abs', 'a')
        ins('move', 'a,x0', '|-1| saturates to the largest word')
        ins('move', f'{x("dk")},y1')
        ins('mpy', 'x0,y1,a', 'a1 = p * depth * 256, an integer')
        ins('move', f'a1,{x("e" + s)}')

    def knob(slot, note):
        ins('move', f'x:(r6+${slot:x}),a', note)
        ins('bsr', 'dv_q')

    pre_k, off_k = ref.PRE_K, ref.OFF_K
    k1 = {s: (ref.RING - seg['ap1' + s] - (ref.AP_L if s == 'L' else ref.AP_R)[0]) * 256 for s in 'LR'}
    k9 = {s: (ref.RING - seg['loop' + s] - (ref.LOOP_L if s == 'L' else ref.LOOP_R)) * 256 for s in 'LR'}

    emit('; DEEPVERB: a true-stereo figure-eight of lossy allpasses, 44.1 kHz.')
    emit('; GENERATED by modules/deepverb/gen_asm.py from deepverb_ref.py -- do not edit;')
    emit('; change the generator or the reference and rerun it.')
    emit('; One 16K Y ring at the allocator buffer, r5 the write head (m5 = $3fff).')
    emit('; Segments, positions behind the head (write at the start, reads after it):')
    for name, at in seg.items():
        emit(f';   {name:<6} {at}')
    emit('; x:(r7+$xx): ' + ', '.join(f'{k} ${v:02x}' for k, v in SLOT.items()))
    emit('; Moving taps are P = (16384 - start - delay) * 256, positive, 8 fraction bits.')
    emit('; Only the mpy/mac forms AGENTS.md lists as signed: x0,y1 and y0,x0.')
    emit('')
    emit('init:')
    ins('move', '#>$ffffff,m4')
    ins('move', 'r7,r4')
    ins('clr', 'a')
    ins('do', f'#${ZERO_WORDS:x},>dv_zero', 'every slot the loop reads')
    ins('move', 'a,x:(r4)+')
    emit('dv_zero:')
    ins('nop')
    ins('move', 'x:>$213,r4', 'the allocator entry: our 16K buffer')
    ins('move', 'x:(r4),a')
    ins('move', f'a,{x("base")}')
    ins('move', f'a,{x("head")}')
    ins('move', '#>$80,a', '128 calls of 128 words clear the ring')
    ins('move', f'a,{x("clear")}')
    ins('rts')
    emit('')
    emit('proc:')
    emit('; Dry until the ring is clean: 128 words a call.')
    ins('move', f'{x("clear")},a')
    ins('tst', 'a')
    ins('beq', 'dv_ready')
    ins('sub', '#>$1,a')
    ins('move', f'a,{x("clear")}')
    ins('asl', '#$7,a,a')
    ins('move', f'{x("base")},x0')
    ins('add', 'x0,a')
    ins('move', 'a,r4')
    ins('move', '#>$ffffff,m4')
    ins('clr', 'a')
    ins('do', '#128,>dv_clear')
    ins('move', 'a,y:(r4)+')
    emit('dv_clear:')
    ins('nop')
    ins('rts')
    emit('dv_ready:')
    knob(0, 'DECAY: u = (1 - q)^2')
    ins('move', '#>$7fffff,a')
    ins('sub', 'x0,a')
    ins('move', 'a,x0')
    ins('move', 'a,y1')
    ins('mpy', 'x0,y1,a')
    ins('move', 'a,x0')
    ins('move', f'#>{q(0.15)},y1', 'absorb = 1 - 0.15u')
    ins('mpy', 'x0,y1,b')
    ins('move', '#>$7fffff,a')
    ins('sub', 'b,a')
    ins('move', f'a,{x("absorb")}')
    ins('move', f'#>{q(0.49)},y1', 'fb = 0.99 - 0.49u')
    ins('mpy', 'x0,y1,b')
    ins('move', f'#>{q(0.99)},a')
    ins('sub', 'b,a')
    ins('move', f'a,{x("fb")}')
    knob(1, 'DAMP: lp = 0.95 - 0.80q')
    ins('move', f'#>{q(0.80)},y1')
    ins('mpy', 'x0,y1,b')
    ins('move', f'#>{q(0.95)},a')
    ins('sub', 'b,a')
    ins('move', f'a,{x("lpc")}')
    knob(2, 'MIX')
    ins('move', f'x0,{x("mix")}')
    knob(3, 'CHORUS: dk = depth*256 = q >> 8, half = q >> 9')
    ins('move', 'x0,a')
    ins('asr', '#$8,a,a')
    ins('move', f'a1,{x("dk")}')
    ins('move', 'x0,a')
    ins('asr', '#$9,a,a')
    ins('move', 'a1,x1')
    for s in 'LR':
        ins('move', f'#>{h(k1[s])},a', f'B1{s} = (16384 - start - m) * 256 + half')
        ins('add', 'x1,a')
        ins('move', f'a,{x("b1" + s)}')
    ins('move', f'{x("dk")},x1')
    for s in 'LR':
        ins('move', f'#>{h(k9[s])},a', f'B9{s} = (16384 - start - L) * 256 - dk')
        ins('sub', 'x1,a')
        ins('move', f'a,{x("b9" + s)}')
    knob(4, f'PRE: q * {pre_k} samples')
    ins('move', f'#>{pre_k},y1')
    ins('mpy', 'x0,y1,a')
    ins('move', f'a1,{x("pre")}', 'the integer, floored')
    ins('move', 'x:(r6+$5),a', 'WIDTH: w/2 of the side gain')
    ins('and', '#>$7f0000,a')
    ins('move', 'a1,x0')
    ins('move', '#>$400000,a')
    ins('cmp', 'x0,a')
    ins('bge', 'dv_wsml')
    ins('move', 'x0,a', 'above 64: 0.5 + (w - 0.5) * 32/63')
    ins('move', '#>$400000,x1')
    ins('sub', 'x1,a')
    ins('move', 'a,x0')
    ins('move', '#>$410410,y1')
    ins('mpy', 'x0,y1,a')
    ins('add', 'x1,a')
    ins('move', f'a,{x("wid")}')
    ins('bra', 'dv_wset')
    emit('dv_wsml:')
    ins('move', f'x0,{x("wid")}', 'up to 64: k/128')
    emit('dv_wset:')
    knob(0xc, 'LOWCUT (slot 6): hp = 0.002 + 0.098q^2')
    ins('move', 'x0,y1')
    ins('mpy', 'x0,y1,a')
    ins('move', 'a,x0')
    ins('move', f'#>{q(0.098)},y1')
    ins('mpy', 'x0,y1,a')
    ins('move', f'#>{q(0.002)},x0')
    ins('add', 'x0,a')
    ins('move', f'a,{x("hpc")}')
    knob(0xd, 'BAL (slot 8): the LATE share')
    ins('move', f'x0,{x("bal")}')
    ins('move', 'x:(r6+$e),a', f'OFFSET (slot 10): (k - 64) * {off_k} / 128 samples')
    ins('and', '#>$7f0000,a')
    ins('move', '#>$400000,x0')
    ins('sub', 'x0,a')
    ins('move', 'a,x0')
    ins('move', f'#>{off_k},y1')
    ins('mpy', 'x0,y1,a')
    ins('move', 'a1,x1', 'floored; drop a0 before adding')
    ins('move', 'x1,a')
    ins('tst', 'a')
    ins('bge', 'dv_opos')
    ins('move', f'{x("pre")},b', 'negative: L later')
    ins('sub', 'a,b')
    ins('move', f'{x("pre")},a')
    ins('bra', 'dv_oset')
    emit('dv_opos:')
    ins('move', f'{x("pre")},b', 'positive: R later')
    ins('add', 'b,a')
    emit('dv_oset:')
    for acc, s in (('b', 'L'), ('a', 'R')):
        ins('move', f'{acc},x0', f'pre {s} -> its read offset')
        ins('move', f'#>{h(-seg["pre" + s])},{acc}')
        ins('sub', f'x0,{acc}')
        ins('move', f'{acc},{x("prn" + s)}')
    emit('; ---- the sample loop ----')
    ins('move', '#>$1,n0')
    ins('move', '#>$3fff,m4')
    ins('move', '#>$3fff,m5')
    ins('move', f'{x("head")},r5')
    ins('do', 'n7,>dv_end')
    lfo('L', ref.LFO_STEP[0])
    lfo('R', ref.LFO_STEP[1])
    side('L')
    side('R')
    emit('; ---- output: BAL, WIDTH, makeup, MIX ----')
    for s in 'LR':
        ins('move', f'{x("early" + s)},b', f'w{s.lower()} = early + bal*(late - early)')
        ins('move', f'{x("late" + s)},a')
        ins('sub', 'b,a')
        ins('move', 'a,x0')
        if s == 'L':
            ins('move', f'{x("bal")},y1')
        ins('mpy', 'x0,y1,a')
        ins('add', 'b,a')
        if s == 'L':
            ins('move', 'a,x1')
    ins('move', 'a,x0')
    ins('move', 'x1,a')
    ins('sub', 'x0,a', 'wl - wr')
    ins('move', 'x1,b')
    ins('add', 'x0,b')
    ins('asr', '#$1,b,b', 'm = (wl + wr) / 2')
    ins('move', 'a,x0')
    ins('move', f'{x("wid")},y1')
    ins('mpy', 'x0,y1,a', 'sd = (wl - wr) * width/2')
    ins('move', 'a,x0')
    ins('move', 'b,a')
    ins('add', 'x0,a', 'L = m + sd')
    ins('sub', 'x0,b', 'R = m - sd')
    ins('asl', f'#${ref.OUT_SHIFT:x},a,a', 'makeup')
    ins('asl', f'#${ref.OUT_SHIFT:x},b,b')
    ins('move', 'b,x1', 'limited: the output clip')
    ins('move', 'a,y0')
    ins('move', f'{x("mix")},y1')
    for s in 'LR':
        ins('move', 'x:(r0),b', f'{s} = dry + mix*(wet - dry)')
        ins('move', 'y0,a' if s == 'L' else 'x1,a')
        ins('sub', 'b,a')
        ins('move', 'a,x0')
        ins('mpy', 'x0,y1,a')
        ins('add', 'b,a')
        ins('move', 'a,x:(r0)+')
    ins('move', f'{x("noutL")},x0')
    ins('move', f'x0,{x("outL")}')
    ins('move', '(r5)+')
    emit('dv_end:')
    ins('nop')
    ins('move', f'r5,{x("head")}')
    ins('move', '#>$ffffff,m4')
    ins('move', '#>$ffffff,m5')
    ins('rts')
    emit('')
    emit('; k/127 of a knob word in a: x0 = w + w/127, saturated. Clobbers a, y1.')
    emit('; After proc: dsp_asm cannot encode a backward bsr.')
    emit('dv_q:')
    ins('and', '#>$7f0000,a', 'an LFO leaves bits 8-15 set')
    ins('move', 'a1,x0')
    ins('move', '#>$010204,y1', '1/127')
    ins('mpy', 'x0,y1,a')
    ins('add', 'x0,a')
    ins('move', 'a,x0', '127 -> the largest word')
    ins('rts')
    emit('')
    text = '\n'.join(out) + '\n'
    labels = [l[:-1] for l in out if l.endswith(':') and not l.startswith(' ')]
    for a in labels:
        for b in labels:
            if a != b and b.startswith(a):
                raise SystemExit(f'label {a} is a prefix of {b} (dsp_asm resolves by prefix)')
    return text


def main():
    text = generate()
    if '--check' in sys.argv:
        if not ASM.exists() or ASM.read_text() != text:
            print(f'{ASM} is not gen_asm.py output; run python3 {Path(__file__).name}')
            return 1
        return 0
    ASM.write_text(text)
    print(f'{ASM}: {sum(1 for l in text.splitlines() if l.startswith("        "))} instructions')
    return 0


if __name__ == '__main__':
    sys.exit(main())
