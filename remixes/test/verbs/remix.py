"""MINI VERB and FIG8 VERB side by side, for comparing them on a unit."""
from remix.schema import Proof, Remix
REMIX = Remix(family="effects", proof=Proof.RENDER, proof_note="`make verify-miniverb`, `make verify-fig8verb`",
              name="verbs", doc="Mini Verb (in DARK REV's row) and FIG8 VERB (a new row) to compare.",
              modules=("MINIVERB", "FIG8VERB"), fallback="NONE")
