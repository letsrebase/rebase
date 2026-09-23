"""Imports every SQLAlchemy model so that `Base.metadata` is complete.

Each task that adds a model appends its import here.
"""

from pigrocrm.core.activities.models import Activity  # noqa: F401
from pigrocrm.core.attivita.models import Attivita  # noqa: F401
from pigrocrm.core.auth.invitation_models import Invitation  # noqa: F401
from pigrocrm.core.auth.magic_models import MagicLinkToken  # noqa: F401
from pigrocrm.core.auth.models import User  # noqa: F401
from pigrocrm.core.auth.pat_models import PersonalAccessToken  # noqa: F401
from pigrocrm.core.auth.refresh_models import RefreshToken  # noqa: F401
from pigrocrm.core.automations.models import AutomationConfig  # noqa: F401
from pigrocrm.core.contracts.models import Contract, RateCard, RenewalAssumption  # noqa: F401
from pigrocrm.core.customers.models import Customer  # noqa: F401
from pigrocrm.core.deals.models import Deal  # noqa: F401
from pigrocrm.core.digest.models import Digest  # noqa: F401
from pigrocrm.core.documents.models import Document, DocumentVersion  # noqa: F401
from pigrocrm.core.drive.models import GoogleDriveAccount  # noqa: F401
from pigrocrm.core.emitter.models import EmitterProfile  # noqa: F401
from pigrocrm.core.fields.models import FieldDefinition  # noqa: F401
from pigrocrm.core.fiscal.models import FiscalProfile  # noqa: F401
from pigrocrm.core.gmail.models import (  # noqa: F401
    EmailDraft,
    GmailKnownAddress,
    GmailMessage,
    GmailMessageLink,
    GoogleAccount,
    GoogleOAuthState,
    PaymentReminder,
)
from pigrocrm.core.invoices.models import (  # noqa: F401
    Invoice,
    InvoiceCounter,
    InvoiceLine,
    InvoiceRegisterGap,
)
from pigrocrm.core.people.models import Person  # noqa: F401
from pigrocrm.core.pipeline.models import PipelineStage  # noqa: F401
from pigrocrm.core.space_settings.models import SpaceSetting  # noqa: F401
from pigrocrm.core.templates.models import Template  # noqa: F401
from pigrocrm.core.timetracking.models import (  # noqa: F401
    Cost,
    CostCategory,
    PeriodLock,
    TimeEntry,
)
from pigrocrm.core.work_units.models import (  # noqa: F401
    Approval,
    WorkUnit,
    WorkUnitTransition,
)
