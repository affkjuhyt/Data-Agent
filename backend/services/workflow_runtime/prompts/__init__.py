"""The shared prompt templates (*.txt). Modules ask for the directory here instead of
walking `__file__` up to it, so moving a module between layers cannot break the path."""
import os

PROMPTS_DIR = os.path.dirname(os.path.abspath(__file__))
