/**
 * Reading a boolean out of a URL, which is not the one-liner it looks like.
 *
 * The router's default parser is `JSON.parse` per value, so a
 * `<Link search={{ scadute: true }}>` arrives here as a real boolean. A hand-typed,
 * bookmarked or hand-edited URL does not: `?scadute=vero` arrives as the string `"vero"`,
 * and the obvious `Boolean(value)` is `true` for **every** non-empty string -- `"false"`
 * and `"0"` included. That turns "no filter" into a filter, on a list whose whole promise
 * is that it shows the rows the count beside it counted.
 *
 * So the rule is the strict one: `true` only for the boolean `true` or the exact string
 * `"true"`, and `undefined` -- the *absence* of a filter -- for everything else, `false`
 * included. `undefined` rather than `false` matters twice: it is what keeps the parameter
 * out of the URL that TanStack rebuilds, and it is what keeps it out of the query object
 * `openapi-fetch` serialises, so `?scadute=false` never reaches an API that treats the
 * presence of the key as the filter.
 *
 * Shared by `routes/app/deal/list.tsx` and `routes/app/invoices/index.tsx` rather than
 * written twice: a route file may export nothing but `Route` (anything else opts the route
 * out of the router plugin's code-splitting), so a helper the two share has nowhere to
 * live except a module of its own -- which is also the only way it can be tested at all.
 */
export function booleanSearchParam(value: unknown): true | undefined {
  return value === true || value === 'true' ? true : undefined
}
