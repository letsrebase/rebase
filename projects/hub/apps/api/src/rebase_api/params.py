"""The query parameters every admin list shares, bounded once.

An unbounded free-text query, cursor or page size reaching the database is a denial of
service with extra steps, so every list route declares these and none its own copy.
`SearchQ`'s bound is `rebase_core.search.SEARCH_MAX_LENGTH` and `Cursor`'s is
`rebase_core.pagination.CURSOR_MAX_LENGTH`; `Limit`'s ceiling is the one every core service
clamps to (`LIST_LIMIT_MAX`, 500), declared here too so OpenAPI shows it.
"""

from typing import Annotated

from fastapi import Query

from rebase_core.pagination import CURSOR_MAX_LENGTH
from rebase_core.search import SEARCH_MAX_LENGTH

LIST_LIMIT_DEFAULT = 100
LIST_LIMIT_MAX = 500

Limit = Annotated[int, Query(ge=1, le=LIST_LIMIT_MAX)]
Offset = Annotated[int, Query(ge=0)]
SearchQ = Annotated[str | None, Query(max_length=SEARCH_MAX_LENGTH)]
Cursor = Annotated[str | None, Query(max_length=CURSOR_MAX_LENGTH)]
