"""Print the hub API's OpenAPI document on stdout, for `pnpm generate:api`.

The document comes from the app object itself: the routes and the Pydantic models
describe it, so there is no server to start and no database to reach.
"""

import json

from rebase_api.main import app

print(json.dumps(app.openapi(), indent=2, ensure_ascii=False))
