"""MINI VERB and DEEP VERB side by side, for comparing them on a unit."""
from remix.schema import Proof, Remix
REMIX = Remix(family="effects", proof=Proof.RENDER, proof_note="`make verify-miniverb`, `make verify-deepverb`",
              name="verbs", doc="Mini Verb (in DARK REV's row) and DEEP VERB (a new row) to compare.",
              modules=("MINIVERB", "DEEPVERB"), fallback="NONE")
