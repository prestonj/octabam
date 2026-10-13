"""FIG8 VERB alone: the development image for its gates; unflashed."""
from remix.schema import Proof, Remix
REMIX = Remix(family="effects", proof=Proof.RENDER, proof_note="`make verify-fig8verb`", name="fig8verb",
              doc="True-stereo figure-eight allpass reverb.",
              modules=("FIG8VERB",), fallback="NONE")
