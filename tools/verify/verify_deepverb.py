#!/usr/bin/env python3
"""DEEP VERB gates on the assembled DSP code, under dsp_host.

    python3 tools/verify/verify_deepverb.py [remix]

With a remix (make check has just built it) the image at out/mainos_bus.bin
is the one tested; with none, the `deepverb` test remix is built here and
the image that was there before is put back.

1. deepverb.asm is gen_asm.py's output (the source cannot drift from the
   reference's layout and laws).
2. The DSP matches deepverb_ref.py, stereo, at five settings: the residual
   stays 40 dB under the reference's level (measured 45-80 dB, 12 Oct 2026:
   24-bit rounding, not a different algorithm).
3. Dry, bit-exact, while the ring is cleared (the first 128 calls).
4. A knob word with bits 8-15 set (an LFO leaves them) renders as the clean
   word does, on every slot the DSP reads; the control proves -pword lands.
5. Eight instances, four a core: each slot's output equals its solo render,
   interleaved cores equal lock-step, and the instruction peak per core per
   block is printed against the stock spring's 20,376.
6. A tail decays: 20 s after a noise burst at DECAY 64 the last second sits
   within 64 LSB of zero (a 24-bit LP/HP state stuck at 596 LSB of DC
   before the 48-bit states, gen_asm.py).
7. Smoothing: each knob jumps 0 -> 127 -> 0 under a steady 440 Hz tone; the
   output's 4th difference (the tone is 96 dB down in it, a step is not)
   stays under a bound per knob, 1.5x what was measured on 12 Oct 2026.
   Unsmoothed, the same test read 27k (LCUT) to 18.6M (BAL); KNOB_JUMP has
   both columns.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import toolpath  # noqa: E402,F401
import send_probe  # noqa: E402
from remix import registry  # noqa: E402

sys.path.insert(0, str(ROOT / 'modules/deepverb'))
import deepverb_ref as ref  # noqa: E402

HOST = os.environ.get('DSP_HOST') or str(ROOT / 'vendor/dsp56300/build/source/dsp_host/dsp_host')
OUT = ROOT / 'out/deepverb_verify'
FR = 16
CLEAR = 128                 # calls before the ring is clean: dry until then
WARM = CLEAR + 32           # blocks of silence before the test audio
SPRING_PEAK = 20376         # stock spring, worst tested, per core per block
# The per-track budget: spring's peak over its four tracks. The ceiling is a
# regression guard, not a target: DEEP VERB measured 121% of the budget
# unsplit and 126% with moving knobs and split calls (12 Oct 2026; 113%
# before the smoothing).
BUDGET = SPRING_PEAK / 4
CEILING = 1.28 * SPRING_PEAK

# Knob jump -> (bound, unsmoothed): the 4th-difference peak in LSB, the tone
# 0.25 of full scale (2.1M LSB). Measured 12 Oct 2026, one instance, MIX 100.
KNOB_JUMP = {
    'DECAY': (13_300, 1_537_688), 'DAMP': (21_700, 207_703), 'MIX': (30_600, 7_229_264),
    'CHOR': (7_500, 884_831), 'PRE': (53_900, 66_513), 'WIDTH': (14_600, 2_125_781),
    'LCUT': (8_900, 26_656), 'BAL': (79_500, 18_576_546), 'OFFST': (56_300, 172_432),
}
SPLITS = [1, 3, 7, 15, 15, 7, 3, 1]

MOD = registry.by_key('DEEPVERB')
NAMES = [p.name.decode() if p.name else '' for p in MOD.params]
# reference knob names -> panel slot names
PANEL = {'CHORUS': 'CHOR', 'LOWCUT': 'LCUT', 'OFFSET': 'OFFST'}

gates = []


def gate(name, ok, detail=''):
    gates.append(dict(name=name, passed=bool(ok), detail=detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)
    if not ok:
        (OUT / 'verification.json').write_text(json.dumps(gates, indent=2))
        raise SystemExit(1)


def params(k):
    v = [p.default or 0 for p in MOD.params]
    for name, val in k.items():
        v[NAMES.index(PANEL.get(name, name))] = val
    return v


def stereo_raw(path, left, right, lead_blocks):
    words = [0] * (lead_blocks * FR * 2)
    words += [int(round(max(-1.0, min(1.0, v)) * 8388607)) for lr in zip(left, right) for v in lr]
    words += [0] * ((-len(words)) % (FR * 2))
    np.array(words, dtype='<i4').tofile(path)
    return len(words) // (FR * 2)


def run(mems, tag, inputs, knobs, instances=1, positions=None, extra=(), lead=WARM):
    """inputs: one (left, right) per instance. Returns ([(L, R)], meter rows)."""
    positions = positions or list(range(instances))
    cores = [p // 4 for p in positions]
    eps = [send_probe.entry_points(mems[c], MOD.menu.fx2_id) for c in cores]
    raws, blocks = [], 0
    for i, (left, right) in enumerate(inputs):
        raw = OUT / f'{tag}_in{i}.raw'
        blocks = max(blocks, stereo_raw(raw, left, right, lead))
        raws.append(str(raw))
    out = OUT / f'{tag}.raw'
    cmd = [HOST, '-mem', str(mems[0]), '-memB', str(mems[1]),
           '-init', ','.join(f'{e[0]:x}' for e in eps),
           '-proc', ','.join(f'{e[1]:x}' for e in eps),
           '-inst', str(instances), '-core', ','.join(map(str, cores)),
           '-alloc', ','.join(str(1 + 2 * (p % 4)) for p in positions),
           '-r7', ','.join(str(2 + 3 * (p % 4)) for p in positions),
           '-audioidx', ','.join(str(p % 4) for p in positions),
           '-allocproc', 'end', '-frames', str(FR), '-blocks', str(blocks),
           '-out', str(out), '-meter', str(out.with_suffix('.meter')),
           '-in', ','.join(raws), '-stereo']
    for k in knobs:
        cmd += ['-params', ','.join(map(str, params(k)))]
    cmd += list(extra)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode:
        raise SystemExit(f'dsp_host {tag}: {r.stdout[-1500:]}\n{r.stderr[-1500:]}')
    tracks = []
    for i in range(instances):
        a = np.fromfile(Path(str(out) + (f'.i{i}' if i else '')), dtype='<i4')
        tracks.append((a[0::2], a[1::2]))
    meter = [[int(v) for v in line.split()] for line in out.with_suffix('.meter').read_text().splitlines()]
    return tracks, meter


def plucks(seconds=3.0):
    """Six decaying harmonic notes, panned alternately, then silence."""
    fs, n = ref.FS, int(seconds * ref.FS)
    left, right = np.zeros(n), np.zeros(n)
    for k, f in enumerate((220, 330, 277, 415, 247, 370)):
        t0, pan = int(k * 0.4 * fs), (0.25 if k % 2 else 0.75)
        t = np.arange(int(0.4 * fs))
        s = 0.4 * np.exp(-t / (0.06 * fs)) * sum(np.sin(2 * np.pi * f * h * t / fs) / h for h in (1, 2, 3, 5))
        left[t0:t0 + len(t)] += s * (1 - pan)
        right[t0:t0 + len(t)] += s * pan
    return list(left), list(right)


def verify(mems):
    OUT.mkdir(parents=True, exist_ok=True)
    gen = subprocess.run([sys.executable, str(ROOT / 'modules/deepverb/gen_asm.py'), '--check'],
                         capture_output=True, text=True)
    gate('deepverb.asm is gen_asm.py output', gen.returncode == 0, gen.stdout.strip())

    left, right = plucks()
    lead = (WARM - CLEAR) * FR          # the LFOs run through the warm-up after the clear
    cases = ({}, dict(CHORUS=127, DECAY=0), dict(CHORUS=127, DECAY=127),
             dict(PRE=127, OFFSET=0, WIDTH=127, BAL=20, LOWCUT=100, DAMP=110),
             dict(MIX=60, PRE=90, OFFSET=127, WIDTH=0))
    for k in cases:
        kk = {**dict(ref.DEFAULTS), 'MIX': 127, **k}
        (dsp,), _ = run(mems, 'ref', [(left, right)], [kk])
        dl, dr = (c[WARM * FR:][:len(left)] / 8388607.0 for c in dsp)
        rl, rr = ref.render([0.0] * lead + left, [0.0] * lead + right, kk)
        rl, rr = np.array(rl[lead:]), np.array(rr[lead:])
        err = np.sqrt(np.mean((dl - rl) ** 2 + (dr - rr) ** 2))
        lvl = np.sqrt(np.mean(rl ** 2 + rr ** 2))
        db = 20 * np.log10(err / lvl)
        gate(f'matches deepverb_ref {k or "defaults"}', db < -40,
             f'residual {db:.1f} dB re the reference ({20 * np.log10(lvl):.1f} dBFS)')

    (dsp,), _ = run(mems, 'clear', [(left, right)], [{}], lead=0)
    src = np.array([int(round(v * 8388607)) for v in left[:CLEAR * FR]])
    srcr = np.array([int(round(v * 8388607)) for v in right[:CLEAR * FR]])
    gate('dry, bit-exact, while the ring is cleared',
         np.array_equal(dsp[0][:CLEAR * FR], src) and np.array_equal(dsp[1][:CLEAR * FR], srcr),
         f'{CLEAR} calls')

    clean, _ = run(mems, 'clean', [(left, right)], [dict(CHORUS=100, PRE=60, OFFSET=20, WIDTH=90,
                                                         LOWCUT=40, BAL=70, MIX=100)])
    words = params(dict(CHORUS=100, PRE=60, OFFSET=20, WIDTH=90, LOWCUT=40, BAL=70, MIX=100))
    dirty = [f'0:{off:x}={(words[off] << 16) | 0xfe00:x}' for off in range(6)]
    dirty += [f'0:{off:x}={(words[slot] << 16) | 0x7f00:x}' for off, slot in ((0xc, 6), (0xd, 8), (0xe, 10))]
    dirt, _ = run(mems, 'dirty', [(left, right)], [dict(CHORUS=100, PRE=60, OFFSET=20, WIDTH=90,
                                                        LOWCUT=40, BAL=70, MIX=100)],
                  extra=['-pword', ','.join(dirty)])
    gate('knob words with bits 8-15 set render as clean ones',
         all(np.array_equal(a, b) for a, b in zip(clean[0], dirt[0])), f'{len(dirty)} words')
    # The control: -pword is honoured (a host that ignored it would pass the
    # gate above with nothing tested).
    moved, _ = run(mems, 'pword', [(left, right)], [dict(CHORUS=100, PRE=60, OFFSET=20, WIDTH=90,
                                                         LOWCUT=40, BAL=70, MIX=100)],
                   extra=['-pword', f'0:0={(words[0] + 1) << 16:x}'])
    gate('control: a raw word with another knob value changes the output',
         not np.array_equal(moved[0][0], clean[0][0]), 'DECAY +1 through -pword')

    inputs = [(left[k * 997:] + [0.0] * (k * 997), right[k * 991:] + [0.0] * (k * 991)) for k in range(8)]
    knobs = [dict(DECAY=40 + 10 * k, CHORUS=16 * k, PRE=15 * k, OFFSET=16 * k, MIX=127) for k in range(8)]
    together, meter = run(mems, 'eight', inputs, knobs, instances=8,
                          extra=['-split', ','.join(map(str, SPLITS))])
    peak = max(max(row[1], row[2]) for row in meter[WARM:])
    gate('eight instances, four a core: peak per core per block', peak <= CEILING,
         f'{peak} (spring {SPRING_PEAK}); {peak / 4 / BUDGET:.0%} of the per-track budget')
    for k in range(8):
        (solo,), _ = run(mems, f'solo{k}', [inputs[k]], [knobs[k]], positions=[k],
                         extra=['-split', str(SPLITS[k])])
        gate(f'slot {k}: output among eight == solo', all(np.array_equal(a, b) for a, b in zip(together[k], solo)))
    skew, _ = run(mems, 'skew', inputs, knobs, instances=8,
                  extra=['-split', ','.join(map(str, SPLITS)), '-skew', '97'])
    gate('interleaved cores == lock-step', all(np.array_equal(a, b) for t, s in zip(together, skew)
                                               for a, b in zip(t, s)))

    rng = np.random.default_rng(1)
    burst = list(rng.uniform(-0.5, 0.5, ref.FS // 2))
    silence = [0.0] * (20 * ref.FS)
    (tail,), _ = run(mems, 'tail', [(burst + silence, burst[::-1] + silence)], [dict(DECAY=64, MIX=127)])
    last = np.r_[tail[0][-ref.FS:], tail[1][-ref.FS:]]
    gate('a tail decays: 20 s after a burst at DECAY 64', np.max(np.abs(last)) <= 64,
         f'last second |max| {int(np.max(np.abs(last)))} LSB, mean {last.mean():+.1f}')

    fs = ref.FS
    t = np.arange(3 * fs)
    tone = list(0.25 * np.sin(2 * np.pi * 440 * t / fs))
    base = params(dict(MIX=100))
    for name, (bound, unsmoothed) in KNOB_JUMP.items():
        slot = NAMES.index(name)
        events = []
        for block, value in ((0, 0), (WARM + fs // FR, 127), (WARM + 2 * fs // FR, 0)):
            row = list(base)
            row[slot] = value
            events.append(','.join(map(str, [block, *row])))
        auto = OUT / f'jump_{name}.csv'
        auto.write_text('\n'.join(events) + '\n')
        (out,), _ = run(mems, f'jump_{name}', [(tone, tone)], [{}], extra=['-paramfile', str(auto)])
        peak = 0
        for ch in out:
            y = ch[WARM * FR:].astype(float)
            peak = max(peak, max(np.max(np.abs(np.diff(y[j - 64:j + 4096], 4))) for j in (fs, 2 * fs)))
        gate(f'{name} jumps 0 -> 127 -> 0: the 4th difference within its bound', peak <= bound,
             f'4th-difference peak {peak:.0f} LSB <= {bound} (unsmoothed {unsmoothed})')

    (OUT / 'verification.json').write_text(json.dumps(gates, indent=2) + '\n')
    print(f'{len(gates)} DEEP VERB gates passed', flush=True)


def build_standalone():
    image = ROOT / 'out/mainos_bus.bin'
    keep = OUT / 'kept_mainos_bus.bin'
    OUT.mkdir(parents=True, exist_ok=True)
    if image.exists():
        shutil.copy(image, keep)
    try:
        r = subprocess.run([sys.executable, 'tools/build/build_bus.py'], cwd=ROOT,
                           env={**os.environ, 'REMIX': 'deepverb', 'XBUS': '1', 'SPEC': '1'},
                           capture_output=True, text=True)
        (OUT / 'build.log').write_text(r.stdout + r.stderr)
        if r.returncode:
            raise SystemExit(f'deepverb build failed: {OUT / "build.log"}')
        with tempfile.TemporaryDirectory() as tmp:
            built = Path(tmp) / 'deepverb.bin'
            shutil.copy(image, built)
            return [send_probe.dump_mem(built, OUT / f'deepverb_{p}.mem', p) for p in 'AB']
    finally:
        if keep.exists():
            shutil.copy(keep, image)
            keep.unlink()
        else:
            image.unlink(missing_ok=True)


if __name__ == '__main__':
    name = sys.argv[1] if len(sys.argv) > 1 else 'deepverb'
    if 'DEEPVERB' not in registry.remix(name).modules:
        print(f'[N/A] verify_deepverb: {name} does not carry DEEPVERB')
        sys.exit(0)
    if len(sys.argv) > 1:
        image = ROOT / 'out/mainos_bus.bin'
        OUT.mkdir(parents=True, exist_ok=True)
        print(f'Verifying DEEP VERB in the selected image: {name}', flush=True)
        mems = [send_probe.dump_mem(image, OUT / f'selected_{p}.mem', p) for p in 'AB']
    else:
        mems = build_standalone()
    verify(mems)
