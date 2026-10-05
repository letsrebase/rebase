#!/usr/bin/env bash
# Brings up a disposable database, migrates it, seeds the admin the E2E specs log
# in as, and starts the API. Idempotent: safe to run again on top of a previous,
# still-running stack (it recreates the container and re-launches the API).
#
# Deliberately independent of whatever else is running on this machine: the
# container name, Postgres port (55433, not the default 5432) and database name
# are all suffixed "-e2e" precisely so this can run next to a developer's own
# Postgres, or next to the manual "Stack recipe" from the task brief, without
# either one seeing the other.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"

# shellcheck source=./e2e-env.sh
source "$REPO_ROOT/apps/web/scripts/e2e-env.sh"

# Fix round 1: a Vite dev server orphaned by a previous, signal-interrupted run
# (e2e-teardown.sh's own comment has the full mechanism) would otherwise sit on
# this port forever, and Playwright's `reuseExistingServer: !CI`
# (playwright.config.ts) would then silently reuse that stale process for
# *this* run instead of starting a fresh one -- observed live as "the suite
# starts passing (or failing) for reasons nobody can reconstruct". Clearing it
# here as well as in teardown means this invariant holds even the first time
# this script ever runs after a crash, not only from the second run onward.
echo "== pigrocrm e2e: clearing a stale frontend dev server on :$PIGROCRM_E2E_WEB_PORT, if any =="
kill_port "$PIGROCRM_E2E_WEB_PORT"

# Same reasoning one port over, and the reason this script's own "idempotent: safe to
# run again on top of a previous, still-running stack" header is true rather than
# merely intended. Without it, the `nohup uv run uvicorn` below dies on "[Errno 48]
# address already in use" while the *previous* run's API keeps answering on that port
# -- so the readiness curl passes, this script prints "stack pronto", and the whole
# suite then runs against a process nobody meant to be there (observed live while
# writing e2e/time-tracking.spec.ts: a stale API held :8000 across a database
# recreation, and every request after login came back 401 with no other symptom).
# `kill_port`, not the pidfile, for the same reason the frontend uses it: the leftover
# may be from a run whose pidfile was already removed by its own teardown.
echo "== pigrocrm e2e: clearing a stale API on :$PIGROCRM_E2E_API_PORT, if any =="
kill_port "$PIGROCRM_E2E_API_PORT"

echo "== pigrocrm e2e: bringing up Postgres on :$PIGROCRM_E2E_PG_PORT =="
docker rm -f "$PIGROCRM_E2E_CONTAINER" >/dev/null 2>&1 || true
docker run --rm -d --name "$PIGROCRM_E2E_CONTAINER" \
  -e POSTGRES_PASSWORD=pigrocrm -e POSTGRES_USER=pigrocrm -e POSTGRES_DB=pigrocrm_e2e \
  -p "$PIGROCRM_E2E_PG_PORT":5432 postgres:17-alpine >/dev/null

echo "== pigrocrm e2e: waiting for Postgres to accept connections =="
until docker exec "$PIGROCRM_E2E_CONTAINER" pg_isready -U pigrocrm >/dev/null 2>&1; do sleep 1; done

echo "== pigrocrm e2e: running migrations =="
(cd packages/core && uv run alembic upgrade head)

echo "== pigrocrm e2e: creating the application role the API connects as =="
# The same boot step the image's CMD runs after the migration (REB-634): it creates
# `pigrocrm_app` with the password PIGROCRM_DATABASE_URL carries, grants it the root's
# tables and sequences, and visits the registry of spaces, which is empty here.
uv run python -m pigrocrm.core.cli ensure-space-defaults

echo "== pigrocrm e2e: seeding the admin the specs log in as, the pipeline, the timesheet template, the cost categories and the fiscal profile =="
uv run python - <<'PY'
from sqlalchemy import create_engine

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.schemas import UserCreate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.config import get_settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.emitter.schemas import LegalEntityUpsert
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.templates.service import TemplateService
from pigrocrm.core.timetracking.categories import CostCategoryService

# As the owner, inside the system actor's scope (REB-634): the azienda below is a
# policied row, and the owner is bound by `FORCE ROW LEVEL SECURITY` like everyone.
engine = create_engine(get_settings().owner_database_url, future=True)
with session_factory(engine)() as session:
    bind_scope(session, Actor.system())
    UserService(session).create(
        UserCreate(email="e2e@pigro.it", password="supersegreta1", nome="E2E", ruolo="admin"),
        Actor.system(),
    )
    # `seed_defaults` is admin-only as of this codebase's own Task 15 review (it now
    # calls `actor.require_admin` itself, not just the REST router) -- the brief's own
    # sample called this with no `actor` at all, which `PipelineService.seed_defaults`
    # no longer accepts. `Actor.system()` is the same actor `createadmin` and this
    # script's own user-seeding step above already use.
    PipelineService(session).seed_defaults(Actor.system())
    # The timesheet's PDF path needs the «Rapporto ore» template row to exist; without
    # it `TimeReportService.render_pdf` raises a `ValidationFailed` naming `pigrocrm
    # seed-templates`, which is the right failure at exactly the wrong time -- halfway
    # through an E2E run rather than while the environment is being built. Seeded here,
    # in the same session as everything else, rather than by shelling out to `pigrocrm
    # seed-templates` and `POST /api/cost-categories/seed`: both of those do precisely
    # what these two lines do (see `cli.seed_templates` and `routers/cost_categories.
    # seed`), and a second process plus an HTTP round trip would only add two more ways
    # for this step to fail. The cost categories come along because the deal's «Ore» tab
    # renders `CostsPanel`, whose category picker is empty without them.
    TemplateService(session).seed_defaults(Actor.system())
    CostCategoryService(session).seed_defaults(Actor.system())
    # The letterhead every rendered PDF carries. `DocumentService.create_from_template`
    # reads the emitter singleton unconditionally, so without this row the timesheet's
    # PDF comes back `404 emitter_profile singleton not found` -- which is honest, and
    # is a configuration step a real deployment performs on the «Impostazioni →
    # Emittente» screen before anyone presses Scarica. Synthetic values: nothing here
    # is a real company and the P.IVA is only shaped like one (11 digits is what
    # `_check_fiscal` requires).
    LegalEntityService(session).upsert_default(
        LegalEntityUpsert(
            ragione_sociale="Studio E2E",
            partita_iva="12345678903",
            indirizzo="Via di Prova 1",
            cap="00100",
            comune="Roma",
            provincia="RM",
            email="e2e@pigro.it",
        ),
        Actor.system(),
    )
    # The regime. `InvoiceService.issue` reads this singleton before it consumes a
    # register number (`_regime`), so without this row every emission -- and therefore
    # the whole of e2e/economics.spec.ts -- fails with `404 fiscal_profile singleton not
    # found`; and `AnalyticsService.get_fiscal_estimate` refuses for the same reason,
    # which is what «Analisi › Fiscale» renders as "Profilo fiscale non configurato".
    # Both are honest failures at exactly the wrong time, and configuring the regime is
    # a step a real installation performs on «Impostazioni → Fiscale» before it issues
    # anything. The three rates are the forfettario's own published numbers, which is
    # also `FiscalProfileUpsert`'s own default for each of them; `codice_regime` has no
    # default and is the whole point of the row.
    FiscalProfileService(session).upsert(
        FiscalProfileUpsert(codice_regime="RF19"),
        Actor.system(),
    )
print("seed complete")
PY

echo "== pigrocrm e2e: starting the API on :$PIGROCRM_E2E_API_PORT =="
nohup uv run uvicorn pigrocrm_api.main:app --port "$PIGROCRM_E2E_API_PORT" \
  >"$PIGROCRM_E2E_API_LOG" 2>&1 &
echo $! >"$PIGROCRM_E2E_API_PIDFILE"

echo "== pigrocrm e2e: waiting for the API to answer =="
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:$PIGROCRM_E2E_API_PORT/openapi.json" >/dev/null 2>&1; then
    echo "stack ready on :$PIGROCRM_E2E_API_PORT (pid $(cat "$PIGROCRM_E2E_API_PIDFILE"))"
    exit 0
  fi
  sleep 1
done

echo "The API did not respond in time. Log:" >&2
cat "$PIGROCRM_E2E_API_LOG" >&2 || true
exit 1
