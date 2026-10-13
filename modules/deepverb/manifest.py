"""DEEPVERB -- a true-stereo figure-eight allpass reverb, an FX2 insert.

Each input side has its own pre-delay, damping and six lossy allpasses,
and its loop delay feeds the other side, so the tail moves between the
sides and a panned source keeps its place. deepverb_ref.py is the
specification (the DSP's arithmetic in float); gen_asm.py writes
deepverb.asm from it. README.md has the design and what is measured.
"""
from remix.schema import (Category, Claims, DspSection, Formatter, Gate, Harness,
                          Kind, MenuEntry, Module, Param, Proof, BusRole, YBase)

_P = Formatter.PLAIN
_BLANK = Param(b"", 0)

MODULE = Module(
    name="deepverb", key="DEEPVERB", kind=Kind.DSP_EFFECT,
    category=Category.TRACK, author="prestonj", author_url="https://github.com/prestonj",
    proof=Proof.HARDWARE, proof_note="the author's unit, OCTABAM11 (remix verbs), 13 Oct 2026: four DEEP VERBs + four FILTERs on tracks 1-4, no hang",
    doc="Stereo figure-eight allpass reverb: deep chorus with a rate, diffusion, width.",
    menu=MenuEntry(fx2_id=0x1b, donor_desc=0x400d58b8,    # DARK REV's descriptor
                   abbr=b"DEEP", fullname=b"DEEP VERB", build_tag=False),
    params=(
        Param(b"DECAY", 96, active=True, formatter=_P,
              doc="RT60 0.4 s .. 42 s; 127 is a near-freeze"),
        Param(b"DAMP", 64, active=True, formatter=_P,
              doc="loop low-pass, higher = darker"),
        Param(b"MIX", 40, active=True, formatter=_P,
              doc="dry/wet; 127 fully wet"),
        Param(b"CHOR", 48, active=True, formatter=_P,
              doc="modulation depth of the moving taps, 0..256 samples (5.8 ms)"),
        Param(b"PRE", 0, active=True, formatter=_P,
              doc="pre-delay, 0..61 ms"),
        Param(b"WIDTH", 64, active=True, formatter=_P,
              doc="wet stereo width: 0 mono, 64 natural, 127 wider"),
        Param(b"LCUT", 16, active=True, formatter=_P,
              doc="loop high-pass, higher thins the tail"),
        _BLANK,
        Param(b"DIFF", 96, active=True, formatter=_P,
              doc="diffusion: 0 grainy (the tap after allpass 2), 127 smooth (after 6)"),
        _BLANK,
        Param(b"RATE", 20, active=True, formatter=_P,
              doc="chorus LFO rate, 0.1..4 Hz (the right side 1.31x); 20 is 0.71 Hz"),
        _BLANK,
    ),
    dsp=DspSection(asm="modules/deepverb/deepverb.asm", priority=16,
                   bus_role=BusRole.NONE, ybase=YBase.NEVER,
                   r7_latch_slot=None, gate_label=None),
    claims=Claims(stock_instance_buffer=True, buffer_words=16384),
    harness=Harness(layout_char="9", is_server=False),
    gates=(Gate('tools/verify/verify_deepverb.py', stage='image'),),
    # The sample loop has no branch: every setting costs the same.
    dear={'DECAY': 127, 'CHOR': 127, 'PRE': 127, 'WIDTH': 127, 'RATE': 127},
)
