# Docling bake-off corpus (PoC-1, DI-01)

The 30 documents of `docs/blueprint/DOCUMENT_INTELLIGENCE_SPEC.md` §11 are not in the repo: their licences differ and the
scans may hold personal data. They live in `BAKEOFF_DOCS` (default `~/.cache/dataagent/docs-bakeoff`). This directory
keeps what identifies them and what was labelled on them:

- `sources.json`: each original by sha256, size and page count (`make bakeoff-render` writes it), with its kind,
  source and licence filled in by hand. A document whose bytes no longer match is not the one the gold describes.
- `gold/<doc_id>/`: the ground truth two annotators reconciled (`layout.json`, `tables.json`, `headings.json`,
  `ocr/page-NNN.txt`), written by `make bakeoff-gold … GOLD_OUT=$PWD/tests/data/docs-bakeoff/gold`.

How to label, the sheet format of tables and the two-annotator check: `model/eval/docs/labelstudio/README.md`.
