#!/usr/bin/env python3
"""Write modules/fig8verb/fig8verb.asm from fig8verb_ref.py's layout and laws.

The ring offsets, gains and control-law constants are computed here from
the reference, so the DSP source and the float specification cannot drift
apart; tools/verify/verify_fig8verb.py refuses a fig8verb.asm that differs
from this script's output.

    python3 modules/fig8verb/gen_asm.py          # rewrite fig8verb.asm
    python3 modules/fig8verb/gen_asm.py --check  # exit 1 if it would change
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fig8verb_ref as ref  # noqa: E402

ASM = HERE / 'fig8verb.asm'
# Fixed-point hygiene, measured 12 Oct 2026: a half-second noise burst, then
# 20 s of silence at DECAY 64, the last second's residue (one instance):
#   neither                        596 LSB of DC (-83 dBFS)  5,485 instr/block
#   HP state 48 bits                15 LSB                   5,613
#   LP and HP states 48 bits         9 LSB (-119 dBFS)       5,741   <- this
#   and every ring store rounded     0                       5,997
# The HP coefficient is as small as 0.002, so a truncated 24-bit state
# sticks, and the loop circulates what it holds.
RND = False
# LP48 is off (12 Oct 2026): without it the residue is the same (40 LSB at
# DECAY 64); it is the HP's small coefficient that sticks. 4 a sample.
LP48 = False
HP48 = True
# The rnd around each 48-bit state update: off (12 Oct 2026). With it the
# DECAY 64 residue was 24 LSB, without it 48 (-105 dBFS), for 8 a sample.
RND48 = False
MASK = 0xFFFFFF

# x:(r7+slot), the instance block. Init zeroes $00..$2f.
SLOT = dict(
    base=0x00, head=0x01, clear=0x02,
    lpc=0x06, hpc=0x07, dk=0x08,
    b1L=0x09, b1R=0x0a, b9L=0x0b, b9R=0x0c, prnL=0x0d, prnR=0x0e,
    phL=0x14, phR=0x15, eL=0x16, eR=0x17,
    lpL=0x18, hpL=0x19, lpR=0x1a, hpR=0x1b, outL=0x1c, outR=0x1d, noutL=0x1e,
    lpLlo=0x24, hpLlo=0x25, lpRlo=0x26, hpRlo=0x27,
    earlyL=0x20, earlyR=0x21, wL=0x22, wR=0x23,
    # the knobs' targets; the live values above glide onto them once a block
    tabsorb=0x30, tfb=0x31, tlpc=0x32, thpc=0x33, tdk=0x34, tmix=0x35, tdiff=0x36,
    twid=0x37, tpre=0x38, pre=0x3a, snap=0x3c, stepL=0x3d, stepR=0x3e,
    # per-sample ramps: the value the sample loop reads, and its step
    cabsorb=0x40, cfb=0x41, cdiff=0x43, cA=0x44, cB=0x45, cD=0x46,
    dabsorb=0x48, dfb=0x49, ddiff=0x4b, dA=0x4c, dB=0x4d, dD=0x4e,
    tA=0x4f, tB=0x50, tD=0x51,
    # the wander: a second phase a side, the per-sample step of e, B's step
    phbL=0x52, phbR=0x53, deL=0x54, deR=0x55, stepbL=0x56, stepbR=0x57,
    # each knob's last masked word: its law runs only when the word changes.
    # Init sets them to -1, which no masked word equals. abd: MIX or WIDTH moved.
    rdecay=0x59, rdamp=0x5a, rmix=0x5b, rchor=0x5c, rpre=0x5d, rwid=0x5e,
    rlcut=0x5f, rdiff=0x60, rrate=0x61, abd=0x62,
)
ZERO_WORDS = 0x63
RAW = ('rdecay', 'rdamp', 'rmix', 'rchor', 'rpre', 'rwid', 'rlcut', 'rdiff', 'rrate')
# Smoothing, once a block. Gains and coefficients glide 1/16 of the way
# (5.8 ms) and land exactly; the tap depth and the pre-delays are
# slew-limited instead, because a jump in a read position is a click:
GLIDE = ('lpc', 'hpc')
SLEW = (('dk', 16), ('pre', 1))    # per block: 1/16 sample, 1 sample
# A step once a block is still a step: the audio-path gains and the tank's
# decay coefficients move a sample at a time, (target - now) / 256 a sample,
# the step taken again each call: the same 5.8 ms exponential as the glide,
# drawn in straight 16-sample pieces. They stop within 255 LSB of the target.
RAMP = ('absorb', 'fb', 'diff', 'A', 'B', 'D')


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

    def interp(note, then=None):
        """a = P (positive, 8 fraction bits) -> a = the interpolated read.
        then: an instruction independent of r4, b, y0 to put between the lua
        and the first read (r4 two instructions ahead of its use)."""
        ins('asr', '#$8,a,a', note + ': a1 the integer, a0 the fraction << 16')
        ins('move', 'a1,n5', 'the ring offset of the older word')
        ins('move', 'a0,b')
        ins('lsr', 'b', 'b1 = f << 15, non-negative; b2 is stale')
        ins('lua', '(r5+n5),r4')
        ins('move', 'b1,y0', 'f, the fraction toward the newer word')
        if then:
            ins(*then)
        else:
            ins('nop', '', 'r4 settles (no stall)')
        ins('move', 'y:(r4)+,b')
        ins('move', 'y:(r4),a')
        ins('sub', 'b,a')
        ins('move', 'a,x0')
        ins('mpy', 'y0,x0,a')
        ins('add', 'b,a')

    def allpass(j, side, start, m, i, o, nxt):
        """Lossy allpass j (1-based): x in accumulator i, d in x0 (the first,
        moving one, read by the caller), y0 = absorb, and n5 already the read
        offset. The output lands in o, the other accumulator. The gain loads
        in parallel with the multiply (stock: mpy x0,x0,a #>$7fffff,y1); the Y
        reads and writes stay plain moves, as stock has no y:(Rn+Nn) in a
        parallel move. Every n5 is loaded two instructions or more before its
        use, so the 56300 does not stall on it: nxt is the next access's
        offset, loaded here."""
        if j > 1:
            ins('move', 'y:(r5+n5),x0', f'AP{j}{side} {m}, g={ref.AP_G[j - 1]}')
            ins('move', f'#>{h(-start)},n5')
        ins('mpy', f'y0,x0,{o}       #>{h(g[j - 1])},y1', 's = absorb * d; g')
        ins('move', f'{o},x0')
        ins('mac', f'-x0,y1,{i}', 'w = x - g*s')
        if RND:
            ins('rnd', i)
        ins('move', f'{i},y:(r5+n5)')
        ins('move', f'#>{h(nxt)},n5')
        ins('move', f'{i},x0')
        ins('mac', f'x0,y1,{o}', 'y = s + g*w')

    def side(s):
        o = 'R' if s == 'L' else 'L'
        ap = ref.AP_L if s == 'L' else ref.AP_R
        loop = ref.LOOP_L if s == 'L' else ref.LOOP_R
        emit(f'; ---- side {s} ----')
        if s == 'L':
            ins('move', f'#>{h(-seg["pre" + s])},n5')
        ins('move', 'x:(r0),a' if s == 'L' else 'x:(r0+n0),a', f'{s} in')
        ins('asr', f'#${ref.IN_SHIFT:x},a,a', 'tank headroom')
        if RND:
            ins('rnd', 'a')
        ins('move', 'a,y:(r5+n5)')
        ins('move', f'{x("prn" + s)},n5', 'pre-delay read, per block')
        ins('move', f'{x("out" + o)},x0', f'the figure-eight: {o} loop output, last sample')
        ins('move', f'{x("cfb")},y1')
        ins('move', 'y:(r5+n5),a')
        ins('mac', 'x0,y1,a')
        ins('move', f'{x("lp" + s)},b', 'one-pole LP, DAMP')
        if LP48:
            ins('move', f'{x("lp" + s + "lo")},b0', 'its low word: the state keeps 48 bits')
        ins('sub', 'b,a')
        if LP48 and RND48:
            ins('rnd', 'a')
        ins('move', 'a,x0')
        ins('move', f'{x("lpc")},y1')
        ins('mpy', 'x0,y1,a')
        ins('add', 'b,a')
        ins('move', f'a,{x("lp" + s)}')
        if LP48:
            ins('move', f'a0,{x("lp" + s + "lo")}')
            if RND48:
                ins('rnd', 'a')
        ins('move', 'a,x1', 'one-pole HP, LOWCUT: x - LP(x)')
        ins('move', f'{x("hp" + s)},b')
        if HP48:
            ins('move', f'{x("hp" + s + "lo")},b0', 'its low word: the state keeps 48 bits')
        ins('sub', 'b,a')
        if HP48 and RND48:
            ins('rnd', 'a')
        ins('move', 'a,x0')
        ins('move', f'{x("hpc")},y1')
        ins('mpy', 'x0,y1,a')
        ins('add', 'b,a')
        ins('move', f'a,{x("hp" + s)}')
        if HP48:
            ins('move', f'a0,{x("hp" + s + "lo")}')
            if RND48:
                ins('rnd', 'a')
        ins('move', 'x1,b')
        ins('sub', 'a,b')
        ins('move', 'b,x1', f'park x - LP(x); AP1{s} {ap[0]} +- depth/2, g={ref.AP_G[0]}')
        ins('move', f'{x("b1" + s)},a')
        ins('move', f'{x("e" + s)},x0')
        ins('sub', 'x0,a', 'P = B1 - e')
        interp('the moving read', ('move', f'#>{h(-seg["ap1" + s])},n5', 'AP1 write, ahead'))
        ins('move', 'a,x0', 'd')
        ins('move', f'{x("cabsorb")},y0', 'absorb, held in y0 through the chain')
        ins('move', 'x1,a', 'x back')
        acc = ('a', 'b')
        for j in range(1, ref.APS + 1):
            i, o = acc[(j - 1) % 2], acc[j % 2]
            if j < ref.APS:
                nxt = -(seg[f'ap{j + 1}{s}'] + ap[j])
            else:
                nxt = -seg['loop' + s]
            allpass(j, s, seg[f'ap{j}{s}'], ap[j - 1], i, o, nxt)
            if j == ref.EARLY_AP:
                ins('move', f'{o},{x("early" + s)}', 'EARLY tap')
        assert acc[ref.APS % 2] == 'a'
        if RND:
            ins('rnd', 'a')
        ins('move', 'a,y:(r5+n5)', f'LATE into the loop delay {loop} + depth - e')
        ins('move', f'{x("early" + s)},b', f'w{s} = early + diff*(late - early)')
        ins('sub', 'b,a')
        ins('move', 'a,x0')
        ins('move', f'{x("cdiff")},y1')
        ins('mpy', 'x0,y1,a')
        ins('add', 'b,a')
        ins('move', f'a,{x("w" + s)}')
        ins('move', f'{x("b9" + s)},a')
        ins('move', f'{x("e" + s)},x0')
        ins('add', 'x0,a', 'P = B9 + e')
        if s == 'L':
            interp('the moving read', ('move', f'#>{h(-seg["preR"])},n5', 'R pre write, ahead'))
        else:
            interp('the moving read', ('move', f'{x("wL")},x1', 'wl, for the output'))
        ins('move', f'a,{x("nout" + s if s == "L" else "outR")}',
            'R reads the old L output this sample' if s == 'L' else 'L has read it')

    def lfo(s):
        ins('move', f'{x("ph" + s)},a', f'LFO {s}: phase += RATE step, p = |phase|')
        ins('move', f'{x("step" + s)},x0')
        ins('add', 'x0,a')
        ins('move', f'a1,{x("ph" + s)}', 'wraps: a1 is stored raw')
        ins('abs', 'a', 'unwrapped: past +1 it is still about 1')
        ins('move', 'a,x0', 'and saturates to the largest word')
        ins('move', f'{x("dk")},y1')
        ins('mpy', 'x0,y1,a', 'a1 = p * depth * 256, an integer')
        ins('move', f'a1,{x("e" + s)}')

    def knob(slot, raw, note, scale=True):
        """Skip to dv_skip_<raw> unless the masked knob word changed. Else
        x0 = k/127 of it (w + w/127, saturated), or the masked word itself
        (scale=False). An LFO leaves bits 8-15 set: mask first. Clobbers a, y1."""
        ins('move', f'x:(r6+${slot:x}),a', note)
        ins('and', '#>$7f0000,a')
        ins('move', f'{x(raw)},x0')
        ins('cmp', 'x0,a')
        ins('beq', f'dv_skip_{raw}')
        ins('move', f'a1,{x(raw)}')
        ins('move', 'a1,x0')
        if scale:
            ins('move', '#>$010204,y1', '1/127')
            ins('mpy', 'x0,y1,a')
            ins('add', 'x0,a')
            ins('move', 'a,x0', '127 -> the largest word')

    def skip(raw):
        emit(f'dv_skip_{raw}:')

    pre_k = ref.PRE_K
    k1 = {s: (ref.RING - seg['ap1' + s] - (ref.AP_L if s == 'L' else ref.AP_R)[0]) * 256 for s in 'LR'}
    k9 = {s: (ref.RING - seg['loop' + s] - (ref.LOOP_L if s == 'L' else ref.LOOP_R)) * 256 for s in 'LR'}

    emit('; FIG8VERB: a true-stereo figure-eight of lossy allpasses, 44.1 kHz.')
    emit('; GENERATED by modules/fig8verb/gen_asm.py from fig8verb_ref.py -- do not edit;')
    emit('; change the generator or the reference and rerun it.')
    emit('; One 16K Y ring at the allocator buffer, r5 the write head (m5 = $3fff).')
    emit('; Segments, positions behind the head (write at the start, reads after it):')
    for name, at in seg.items():
        emit(f';   {name:<6} {at}')
    emit('; x:(r7+$xx): ' + ', '.join(f'{k} ${v:02x}' for k, v in SLOT.items()))
    emit('; Moving taps are P = (16384 - start - delay) * 256, positive, 8 fraction bits.')
    emit('; mpy/mac operand pairs: x0,y1 and y0,x0 (AGENTS.md), x1,y0 and y1,x1 (stock runs both).')
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
    ins('move', '#>$ffffff,x0', 'no masked knob word is -1: every law runs once')
    for raw in RAW:
        ins('move', f'x0,{x(raw)}')
    ins('move', '#>$80,a', '128 calls of 128 words clear the ring')
    ins('move', f'a,{x("clear")}')
    ins('rts')
    emit('')
    emit('proc:')
    emit('; a = 0: the first sub-block of a split block (the dispatcher calls again')
    emit('; with a != 0 for the rest). It does no per-block work: the ramps, the')
    emit('; wander and the slews carry on from the last call.')
    ins('tst', 'a')
    ins('beq', 'dv_sub')
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
    emit('dv_sub:')
    ins('move', f'{x("clear")},a', 'dry while the ring clears')
    ins('tst', 'a')
    ins('bne', 'dv_none')
    ins('move', f'{x("snap")},a', 'and before the first full call')
    ins('tst', 'a')
    ins('beq', 'dv_none')
    ins('bra', 'dv_loop')
    emit('dv_none:')
    ins('rts')
    emit('dv_ready:')
    emit('; Each knob: 5 instructions when its word has not changed, its law when it has.')
    knob(0, 'rdecay', 'DECAY: u = (1 - q)^2')
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
    ins('move', f'a,{x("tabsorb")}')
    ins('move', f'#>{q(0.49)},y1', 'fb = 0.99 - 0.49u')
    ins('mpy', 'x0,y1,b')
    ins('move', f'#>{q(0.99)},a')
    ins('sub', 'b,a')
    ins('move', f'a,{x("tfb")}')
    skip('rdecay')
    knob(1, 'rdamp', 'DAMP: lp = 0.95 - 0.80q')
    ins('move', f'#>{q(0.80)},y1')
    ins('mpy', 'x0,y1,b')
    ins('move', f'#>{q(0.95)},a')
    ins('sub', 'b,a')
    ins('move', f'a,{x("tlpc")}')
    skip('rdamp')
    knob(2, 'rmix', 'MIX')
    ins('move', f'x0,{x("tmix")}')
    ins('move', '#>$1,a')
    ins('move', f'a,{x("abd")}')
    skip('rmix')
    knob(3, 'rchor', 'CHORUS: dk = depth*256 = q >> 7')
    ins('move', 'x0,a')
    ins('asr', '#$7,a,a')
    ins('move', f'a1,{x("tdk")}')
    skip('rchor')
    knob(4, 'rpre', f'PRE: q * {pre_k} samples')
    ins('move', f'#>{pre_k},y1')
    ins('mpy', 'x0,y1,a')
    ins('move', f'a1,{x("tpre")}', 'the integer, floored')
    skip('rpre')
    knob(5, 'rwid', 'WIDTH: w/2 of the side gain', scale=False)
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
    ins('move', f'a,{x("twid")}')
    ins('bra', 'dv_wset')
    emit('dv_wsml:')
    ins('move', f'x0,{x("twid")}', 'up to 64: k/128')
    emit('dv_wset:')
    ins('move', '#>$1,a')
    ins('move', f'a,{x("abd")}')
    skip('rwid')
    ins('move', f'{x("abd")},a', 'MIX or WIDTH moved:')
    ins('tst', 'a')
    ins('beq', 'dv_skip_abd')
    ins('clr', 'a')
    ins('move', f'a,{x("abd")}')
    ins('move', f'{x("twid")},a', 'A, B = mix * (1/4 +- width/4), D = (1 - mix) / 8')
    ins('asr', '#$1,a,a')
    ins('move', 'a,x1')
    ins('move', '#>$200000,a')
    ins('add', 'x1,a')
    ins('move', 'a,y1')
    ins('move', f'{x("tmix")},x0')
    ins('mpy', 'x0,y1,a')
    ins('move', f'a,{x("tA")}')
    ins('move', '#>$200000,a')
    ins('sub', 'x1,a')
    ins('move', 'a,y1')
    ins('mpy', 'x0,y1,a')
    ins('move', f'a,{x("tB")}')
    ins('move', '#>$7fffff,a')
    ins('sub', 'x0,a')
    ins('asr', f'#${ref.MIX_SHIFT:x},a,a')
    ins('move', f'a,{x("tD")}')
    emit('dv_skip_abd:')
    knob(0xc, 'rlcut', 'LOWCUT (slot 6): hp = 0.002 + 0.098q^2')
    ins('move', 'x0,y1')
    ins('mpy', 'x0,y1,a')
    ins('move', 'a,x0')
    ins('move', f'#>{q(0.098)},y1')
    ins('mpy', 'x0,y1,a')
    ins('move', f'#>{q(0.002)},x0')
    ins('add', 'x0,a')
    ins('move', f'a,{x("thpc")}')
    skip('rlcut')
    knob(0xd, 'rdiff', 'DIFF (slot 8): the LATE share')
    ins('move', f'x0,{x("tdiff")}')
    skip('rdiff')
    knob(0xe, 'rrate', 'RATE (slot 10): the wander phases\' steps, x16 (a block at a time)')
    for slot, (base, span) in (('stepL', ref.RATE_L), ('stepR', ref.RATE_R),
                               ('stepbL', ref.RATE_LB), ('stepbR', ref.RATE_RB)):
        ins('move', f'#>{span},y1', f'{slot}: {base} + q * {span}')
        ins('mpy', 'x0,y1,a')
        ins('move', 'a1,x1', 'floored; drop a0')
        ins('move', f'#>{base},a')
        ins('add', 'x1,a')
        ins('asl', '#$4,a,a')
        ins('move', f'a,{x(slot)}')
    skip('rrate')
    emit('; ---- smoothing: the first ready call snaps, every later one glides ----')
    ins('move', f'{x("snap")},a')
    ins('tst', 'a')
    ins('bne', 'dv_glide')
    for name in GLIDE + tuple(n for n, _ in SLEW):
        ins('move', f'{x("t" + name)},x0')
        ins('move', f'x0,{x(name)}')
    for name in RAMP:
        ins('move', f'{x("t" + name)},x0')
        ins('move', f'x0,{x("c" + name)}')
    ins('move', '#>$1,a')
    ins('move', f'a,{x("snap")}')
    ins('bra', 'dv_derive')
    emit('dv_glide:')
    for name in GLIDE:
        ins('move', f'{x("t" + name)},x0', f'{name} += (target - {name}) / 16, landing exactly')
        ins('move', f'{x(name)},b')
        ins('move', 'x0,a')
        ins('sub', 'b,a')
        ins('asr', '#$4,a,a')
        ins('move', 'a,x1', 'drop a0: the compare below is 56 bits')
        ins('move', 'x1,a')
        ins('add', 'b,a')
        ins('cmp', 'b,a')
        ins('teq', 'x0,a', 'a step that floors to 0: the target')
        ins('move', f'a,{x(name)}')
    for name, step in SLEW:
        ins('move', f'{x("t" + name)},a', f'{name} moves at most {step} a block')
        ins('move', f'{x(name)},b')
        ins('sub', 'b,a')
        ins('move', f'#>{step},x0')
        ins('cmp', 'x0,a')
        ins('tgt', 'x0,a')
        ins('move', f'#>{h(-step)},x0')
        ins('cmp', 'x0,a')
        ins('tlt', 'x0,a')
        ins('add', 'b,a')
        ins('move', f'a,{x(name)}')
    emit('dv_derive:')
    ins('move', f'{x("dk")},a', 'half = dk / 2')
    ins('asr', '#$1,a,a')
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
    for s in 'LR':
        ins('move', f'{x("pre")},x0', f'pre -> the {s} read offset')
        ins('move', f'#>{h(-seg["pre" + s])},a')
        ins('sub', 'x0,a')
        ins('move', f'a,{x("prn" + s)}')
    for name in RAMP:
        ins('move', f'{x("t" + name)},a', f'the {name} ramp: (target - now) / 256 a sample')
        ins('move', f'{x("c" + name)},x0')
        ins('sub', 'x0,a')
        ins('asr', '#$8,a,a')
        ins('move', f'a,{x("d" + name)}')
    emit('; ---- the wander: a target 16 samples on, once a full call ----')
    emit('; From one a != 0 call to the next is 16 samples whatever the split, so')
    emit('; the step is (target - e) / 16: a split block\'s first call carries the')
    emit('; same straight piece on and lands on the target, never past it.')
    for sd in 'LR':
        ins('move', f'{x("ph" + sd)},a', f'{sd}: A and B on 16 samples; p = (|A| + |B|) / 2')
        ins('move', f'{x("step" + sd)},x0')
        ins('add', 'x0,a')
        ins('move', f'a1,{x("ph" + sd)}', 'wraps: a1 is stored raw')
        ins('abs', 'a', 'unwrapped')
        ins('move', f'{x("phb" + sd)},b')
        ins('move', f'{x("stepb" + sd)},x0')
        ins('add', 'x0,b')
        ins('move', f'b1,{x("phb" + sd)}')
        ins('abs', 'b')
        ins('add', 'a,b')
        ins('asr', '#$1,b,b')
        ins('move', 'b,x0', 'p, saturated at the largest word')
        ins('move', f'{x("dk")},y1')
        ins('mpy', 'x0,y1,a', 'a1 = the target, p * dk')
        ins('move', 'a1,x1')
        ins('move', 'x1,a')
        ins('move', f'{x("e" + sd)},x0')
        ins('sub', 'x0,a')
        ins('asr', '#$4,a,a', '(target - e) / 16, floored')
        ins('move', f'a1,{x("de" + sd)}')
    emit('; ---- the sample loop ----')
    emit('dv_loop:')
    ins('move', '#>$1,n0')
    ins('move', '#>$3fff,m4')
    ins('move', '#>$3fff,m5')
    ins('move', f'{x("head")},r5')
    ins('do', 'n7,>dv_end')
    for name in RAMP:
        ins('move', f'{x("c" + name)},a', f'{name} ramps')
        ins('move', f'{x("d" + name)},x0')
        ins('add', 'x0,a')
        ins('move', f'a,{x("c" + name)}')
    for sd in 'LR':
        ins('move', f'{x("e" + sd)},a', f'e{sd} += de{sd}: the wander, a straight piece a call')
        ins('move', f'{x("de" + sd)},x0')
        ins('add', 'x0,a')
        ins('move', f'a,{x("e" + sd)}')
    side('L')
    side('R')
    emit('; ---- output: L = A*wl + B*wr + D*dryL, R = A*wr + B*wl + D*dryR, /8 ----')
    ins('move', f'{x("wR")},x0', 'wr; wl is in x1')
    ins('move', f'{x("cA")},y0')
    ins('move', f'{x("cB")},y1')
    ins('mpy', 'x1,y0,a', 'A*wl')
    ins('mac', 'x0,y1,a', '+ B*wr')
    ins('mpy', 'y0,x0,b', 'A*wr')
    ins('mac', 'y1,x1,b', '+ B*wl')
    ins('move', f'{x("cD")},y0')
    for acc in 'ab':
        ins('move', 'x:(r0),x0', 'dry')
        ins('mac', f'y0,x0,{acc}')
        ins('asl', f'#${ref.MIX_SHIFT:x},{acc},{acc}', 'x8; the store limits: the output clip')
        ins('move', f'{acc},x:(r0)+')
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
