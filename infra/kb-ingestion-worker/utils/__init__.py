"""Support code for the ingestion worker — config, transport, parsing, logging.

The package top level holds only the pipeline stages (silver, gold,
conflict_resolver) and the entry point (main.py); everything they lean on lives
here. Imported as ``from utils import config as cfg`` etc. (the worker directory
is on PYTHONPATH, so ``utils`` resolves as a package)."""
