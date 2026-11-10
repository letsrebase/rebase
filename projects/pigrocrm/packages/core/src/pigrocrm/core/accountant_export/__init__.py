"""The period bundle for the accountant (REB-1011, spec 2026-11-09 accountant export, § 1).

One ZIP for one legal entity and one period, with an index naming every part, its files, its
row counts and every part left out and why. A part is a `PartDefinition` in `registry.py`;
a project that ships a new source (the purchase register, the IVA settlements, the bank
movements) adds its definition there and the bundle and its index carry it.
"""
