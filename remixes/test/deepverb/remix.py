"""DEEP VERB alone: the development image for its gates; unflashed."""
from remix.schema import Proof, Remix
REMIX = Remix(family="effects", proof=Proof.RENDER, proof_note="`make verify-deepverb`", name="deepverb",
              doc="True-stereo figure-eight allpass reverb.",
              modules=("DEEPVERB",), fallback="NONE")
