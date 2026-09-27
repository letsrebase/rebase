"""The registry side of rebase's engagements door (spec 2026-09-25 § 2.2): the
`RebaseEngagement` row `tenants/database.py` imports by module, and A4's own
`EngagementService` reads and writes through it.

No import here, on purpose. `tenants/database.py` imports
`pigrocrm.core.engagements.models` to make `ensure_tenants_database`'s
`create_all` see `RebaseEngagement` (the same reason it imports
`pigrocrm.core.identity.models`), which runs this `__init__` first. If it ever
re-exported `service`, that import would chain `service -> tenants.service ->
tenants.database` back into a module `tenants/database.py` is still in the
middle of initialising. A4's service is imported by its full path,
`pigrocrm.core.engagements.service`, by every caller -- never through this
package.
"""
