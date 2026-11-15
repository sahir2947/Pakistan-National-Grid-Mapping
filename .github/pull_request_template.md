## What this changes

<!-- What changed and why. Link the issue where the owner agreed to it: changes are by permission only (CONTRIBUTING.md). -->

## Checks

- [ ] `python -m pytest -q` passes
- [ ] `python -m ruff check .` and `python -m mypy` are clean
- [ ] Grid IDs and codes are unchanged
- [ ] A changed stored format has a new format version
- [ ] The map viewer's JavaScript agrees with Python (if a shared rule changed)
- [ ] Generated files regenerated (`pakgrid export-layout`, `pakgrid export-conformance`), if needed
- [ ] Tests added, `project.md` updated, and a line in `CHANGELOG.md` under Unreleased
