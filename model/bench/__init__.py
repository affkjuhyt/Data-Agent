"""Serving-latency benchmark for the two vLLM tiers behind LiteLLM.

`model/eval` answers "is the answer right". This answers "what did it cost to
produce" — time to first token, decode speed, and what happens to both when
more than one request is in flight. Two separate questions, two separate
packages, one shared corpus: the prompts benchmarked here are built from the
real system prompts and the real eval cases, so a number measured here is a
number about the prompts the product actually sends.
"""
