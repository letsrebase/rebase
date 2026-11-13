"""The referral settings and the reward's state machine (P-REB-44): the defaults, one step at a
time, the price before the confirmation, and the writers that race each other.
"""

import threading
from decimal import Decimal

import pytest
from admins import an_admin
from referral_fixtures import (
    _card,
    _engagement_sent,
    _member,
    _request,
    _sign,
    _signed_reward,
    clean_referrals,  # noqa: F401  (fixture)
)
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_core.db import session_factory
from rebase_core.errors import ValidationFailed
from rebase_core.models import (
    Company,
    ReferralReward,
    User,
)
from rebase_core.referral_ledger import ReferralLedgerService
from rebase_core.referral_settings import ReferralSettingsService
from rebase_core.referrals import ReferralService

# ---- the admin's two rates and ledger ------------------------------------------------


def test_get_settings_seeds_the_documented_defaults(clean_referrals: Session) -> None:
    settings = ReferralSettingsService(clean_referrals).get_settings()
    assert (settings.rate_freelancer, settings.rate_company) == (
        Decimal("0.1000"),
        Decimal("0.3000"),
    )


def test_save_settings_updates_the_one_row(clean_referrals: Session) -> None:
    admin_id = an_admin(clean_referrals)
    service = ReferralSettingsService(clean_referrals)
    before = service.get_settings()

    after = service.save_settings(Decimal("0.12"), Decimal("0.35"), admin_id)

    assert after.id == before.id
    assert (after.rate_freelancer, after.rate_company) == (Decimal("0.1200"), Decimal("0.3500"))
    assert after.updated_by == admin_id


def test_set_state_moves_one_step_at_a_time(clean_referrals: Session) -> None:
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)
    admin_id = an_admin(clean_referrals)
    freelancer_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals)
    _sign(clean_referrals, _engagement_sent(clean_referrals, freelancer_id, company_id))
    reward = clean_referrals.scalar(select(ReferralReward))
    assert reward is not None
    service = ReferralLedgerService(clean_referrals)

    with pytest.raises(ValidationFailed, match="da da_confermare si passa solo a confermato"):
        service.set_state(reward.id, "paid", admin_id)

    confirmed = service.set_state(reward.id, "confirmed", admin_id)
    assert confirmed.status == "confirmed"
    assert confirmed.confirmed_by == admin_id
    assert confirmed.confirmed_at is not None

    paid = service.set_state(reward.id, "paid", admin_id)
    assert paid.status == "paid"
    assert paid.paid_at is not None


def test_set_price_only_before_confirmation(clean_referrals: Session) -> None:
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)
    admin_id = an_admin(clean_referrals)
    freelancer_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals)
    _sign(clean_referrals, _engagement_sent(clean_referrals, freelancer_id, company_id))
    reward = clean_referrals.scalar(select(ReferralReward))
    assert reward is not None
    service = ReferralLedgerService(clean_referrals)

    priced = service.set_price(reward.id, Decimal("100.00"), Decimal("10.00"))
    assert (priced.base_amount, priced.reward_amount) == (Decimal("100.00"), Decimal("10.00"))

    service.set_state(reward.id, "confirmed", admin_id)
    with pytest.raises(ValidationFailed, match="prima della conferma"):
        service.set_price(reward.id, Decimal("1"), Decimal("1"))


def test_a_stale_writer_cannot_reprice_a_reward_another_admin_just_confirmed(
    clean_referrals: Session,
) -> None:
    """REB-566: `set_price` read `status`, checked it, then wrote, with no lock. A second
    session that had loaded the reward before the confirmation committed still saw
    `to_confirm` and wrote its price over the confirmed figure. The locking read
    re-reads the row, so the stale session is refused."""
    admin_id = an_admin(clean_referrals)
    reward_id = _signed_reward(clean_referrals, admin_id)
    before = clean_referrals.get(ReferralReward, reward_id)
    assert before is not None
    confirmed_amount = before.reward_amount

    with session_factory(clean_referrals.get_bind())() as stale:  # type: ignore[arg-type]
        loaded = stale.get(ReferralReward, reward_id)
        assert loaded is not None and loaded.status == "to_confirm"

        ReferralLedgerService(clean_referrals).set_state(reward_id, "confirmed", admin_id)

        with pytest.raises(ValidationFailed, match="prima della conferma"):
            ReferralLedgerService(stale).set_price(reward_id, Decimal("1.00"), Decimal("0.10"))

    clean_referrals.expire_all()
    stored = clean_referrals.get(ReferralReward, reward_id)
    assert stored is not None
    assert stored.status == "confirmed"
    assert stored.reward_amount == confirmed_amount


def test_a_stale_writer_cannot_confirm_a_reward_twice(clean_referrals: Session) -> None:
    """REB-566: the same check-then-write gap in `set_state`: two admins confirming the
    same reward must leave the first one's signature and timestamp, and the second is
    told the reward has moved on."""
    admin_id = an_admin(clean_referrals)
    other_admin = User(email="anna@rebase.it", first_name="Anna", last_name="", role="admin")
    clean_referrals.add(other_admin)
    clean_referrals.commit()
    reward_id = _signed_reward(clean_referrals, admin_id)

    with session_factory(clean_referrals.get_bind())() as stale:  # type: ignore[arg-type]
        loaded = stale.get(ReferralReward, reward_id)
        assert loaded is not None and loaded.status == "to_confirm"

        ReferralLedgerService(clean_referrals).set_state(reward_id, "confirmed", admin_id)

        with pytest.raises(ValidationFailed, match="da confermato si passa solo a pagato"):
            ReferralLedgerService(stale).set_state(reward_id, "confirmed", other_admin.id)

    clean_referrals.expire_all()
    stored = clean_referrals.get(ReferralReward, reward_id)
    assert stored is not None
    assert stored.confirmed_by == admin_id


def test_a_price_waits_for_the_confirmation_in_flight_then_is_refused(
    clean_referrals: Session,
) -> None:
    """REB-566, Greptile on fb9dd39a2: the two stale-writer tests above finish the first
    write before the second starts, so they would pass without the row lock too. This is
    the overlap: `clean_referrals` has moved the reward to `confirmed`, locked and uncommitted,
    and a second session's `set_price` starts meanwhile. It must block on the locking
    read and, once `clean_referrals` commits, see `confirmed` and refuse. Without `FOR UPDATE` the
    plain read returns the committed `to_confirm`, the check passes, and only the
    final UPDATE waits, then writes the price over the confirmed figure: no error."""
    admin_id = an_admin(clean_referrals)
    reward_id = _signed_reward(clean_referrals, admin_id)
    row = clean_referrals.get(ReferralReward, reward_id, with_for_update=True)
    assert row is not None
    confirmed_amount = row.reward_amount
    row.status = "confirmed"
    row.confirmed_by = admin_id
    clean_referrals.flush()

    factory = session_factory(clean_referrals.get_bind())  # type: ignore[arg-type]
    outcome: list[BaseException] = []
    started = threading.Event()
    backend: list[int] = []

    def price_from_another_session() -> None:
        # The session is opened, used and closed on this thread alone.
        with factory() as other:
            try:
                backend.append(other.execute(text("SELECT pg_backend_pid()")).scalar_one())
                started.set()
                ReferralLedgerService(other).set_price(reward_id, Decimal("1.00"), Decimal("0.10"))
            except ValidationFailed as exc:
                outcome.append(exc)
            finally:
                other.rollback()
                started.set()

    worker = threading.Thread(target=price_from_another_session)
    worker.start()
    try:
        assert started.wait(timeout=5), "the second session never connected"
        # Wait until the database itself reports the worker parked on a lock inside the
        # locking SELECT. Without `FOR UPDATE` that read never waits: the worker's first
        # wait would be the UPDATE at commit, whose query text is not a SELECT. A
        # transaction caches `pg_stat_activity` after its first read, and `clean_referrals` stays
        # in one until the commit below, so each poll drops that cache first.
        for _ in range(100):
            clean_referrals.execute(text("SELECT pg_stat_clear_snapshot()"))
            blocked = clean_referrals.execute(
                text(
                    "SELECT 1 FROM pg_stat_activity WHERE pid = :pid "
                    "AND wait_event_type = 'Lock' AND query ILIKE 'SELECT%FOR UPDATE%'"
                ),
                {"pid": backend[0]},
            ).first()
            if blocked is not None:
                break
            worker.join(timeout=0.05)
        assert blocked is not None, "set_price did not wait on the row the confirmation holds"
    finally:
        # Release the row whatever happened above, so a failed assertion above never
        # leaves the worker parked on a lock the fixture's teardown then waits on.
        clean_referrals.commit()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert len(outcome) == 1 and "prima della conferma" in str(outcome[0])
    clean_referrals.expire_all()
    stored = clean_referrals.get(ReferralReward, reward_id)
    assert stored is not None
    assert stored.reward_amount == confirmed_amount


def test_list_rewards_shows_a_referral_before_any_letter_is_signed(
    clean_referrals: Session,
) -> None:
    """Regression: the ledger used to start from `ReferralReward`, so a referral
    that had only just signed up, with no letter signed yet, never had a row at all
    -- the admin had no way to tell it existed (Greptile, P-REB-44)."""
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)
    _card(clean_referrals, ref=code)
    service = ReferralLedgerService(clean_referrals)

    page = service.list_rewards()

    assert len(page.items) == 1
    item = page.items[0]
    assert item.referred_name == "Ada Lovelace"
    assert item.reward_id is None
    assert item.status is None
    assert item.rate is None
    assert (item.base_amount, item.reward_amount) == (None, None)


def test_set_state_refuses_to_confirm_an_unpriced_reward(clean_referrals: Session) -> None:
    """The same rule `set_price` already enforces from the other side (Greptile,
    P-REB-44): a fixed-price letter with no estimate leaves a reward with no figure,
    and confirming it anyway would make it unpriceable forever, since `set_price`
    itself only accepts `to_confirm`."""
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)
    admin_id = an_admin(clean_referrals)
    freelancer_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals)
    key = _engagement_sent(
        clean_referrals, freelancer_id, company_id, mode="flat", expected_days=None
    )
    _sign(clean_referrals, key)
    reward = clean_referrals.scalar(select(ReferralReward))
    assert reward is not None and reward.reward_amount is None
    service = ReferralLedgerService(clean_referrals)

    with pytest.raises(ValidationFailed, match="prezzato prima di confermarlo"):
        service.set_state(reward.id, "confirmed", admin_id)


def test_reward_reads_the_engagements_own_budget_snapshot_not_a_later_company_edit(
    clean_referrals: Session,
) -> None:
    """Regression: this used to re-read `Company.daily_budget` live, at signing
    time; an admin editing the company's request between the engagement and the signature
    would silently change a reward the engagement had already promised (Greptile,
    P-REB-44). `hub_engagements.company_daily_budget` snapshots it when the hub opens the
    engagement, the same way the fee is the letter's own."""
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    freelancer_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals)
    key = _engagement_sent(clean_referrals, freelancer_id, company_id)

    company = clean_referrals.get(Company, company_id)
    assert company is not None
    company.daily_budget = Decimal("2000")
    clean_referrals.commit()

    _sign(clean_referrals, key)

    reward = clean_referrals.scalar(select(ReferralReward))
    assert reward is not None
    # (800 - 450) * 20, the budget when the engagement was opened, never the 2000 it became.
    assert reward.base_amount == Decimal("7000.00")
