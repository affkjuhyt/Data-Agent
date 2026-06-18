# Vulture whitelist — names that are USED dynamically (so static analysis can't
# see the reference) and must NOT be reported as dead code.
#
# Add entries as `module_attr` access or bare names that vulture flags but are
# real (e.g. FastAPI dependency callables, pytest fixtures, Pydantic/ORM fields
# referenced only by string, lifecycle hooks). Keep this list curated: each
# entry is a deliberate "this is alive" assertion, reviewed like code.
#
# Usage: `vulture` reads this via [tool.vulture].paths in pyproject.toml.
# (Intentionally empty at Phase 0 — populate as the first dead-code sweep vets
# vulture's findings.)
