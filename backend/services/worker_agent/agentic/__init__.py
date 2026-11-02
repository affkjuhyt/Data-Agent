"""The dynamic analyst agent (docs/superpowers/specs/2026-11-02-dynamic-agent-design.md).

A question is answered by a plan the model makes for it, walked as a graph of nodes (graph.py)
over an open set of tools (tools/), each writing datasets to the turn's workspace, and an answer
the model composes as typed blocks (blocks.py) whose figures the server fills and checks.
"""
