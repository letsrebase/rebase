def escape_like(term: str) -> str:
    r"""Escapes SQL LIKE/ILIKE metacharacters in a user-supplied search term.

    Unescaped, "%" means "any run of characters" and "_" means "any one character" to
    LIKE/ILIKE, so a user searching for a literal "_" or "%" (a VAT-number fragment, a
    discount code, ...) would match unrelated rows that merely have *some* character in
    that position. This is a correctness bug, not a SQL-injection risk -- callers bind
    the escaped term as a parameter, never interpolate it into SQL text -- but it is
    exactly the kind of thing a user notices the first time they search for one.

    The backslash is escaped first, before "%" and "_": escaping either of those first
    would double-escape the backslashes that step introduces. Pass the result to
    `.ilike(pattern, escape="\\")` -- a single backslash -- so the database applies the
    same escape convention this function assumes; Postgres's own default LIKE escape
    character is also backslash, but stating it explicitly keeps this correct even if
    that default is ever overridden per-call.

    Shared across every entity offering free-text search (Organizations today, People and
    Deals next) -- kept here once rather than reimplemented per domain.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
