"""Imports every SQLAlchemy model so that `Base.metadata` is complete.

Each task that adds a model appends its import here.
"""

from pigrocrm.core.accountant_export.models import (  # noqa: F401
    AccountantExportRow,
    ExportDriveWrite,
    ExportShareCode,
    ExportShareLink,
    ExportShareOpening,
)
from pigrocrm.core.activities.models import Activity  # noqa: F401
from pigrocrm.core.ai.models import AiModelConfig, AiModelUsage  # noqa: F401
from pigrocrm.core.auth.invitation_models import Invitation  # noqa: F401
from pigrocrm.core.auth.magic_models import MagicLinkToken  # noqa: F401
from pigrocrm.core.auth.models import User, UserLegalEntity  # noqa: F401
from pigrocrm.core.auth.pat_models import PersonalAccessToken  # noqa: F401
from pigrocrm.core.auth.refresh_models import RefreshToken  # noqa: F401
from pigrocrm.core.automations.models import AutomationConfig  # noqa: F401
from pigrocrm.core.bank.models import (  # noqa: F401
    BankAccount,
    BankMovement,
    BankStatement,
    OrganizationIban,
    StatementMovement,
)
from pigrocrm.core.collaborators.models import (  # noqa: F401
    Engagement,
    EngagementNotice,
    LetterCounter,
    MonthlyReport,
)
from pigrocrm.core.contact_points.models import ContactPoint  # noqa: F401
from pigrocrm.core.contract_emails.models import ContractEmailTemplate  # noqa: F401
from pigrocrm.core.contract_expenses.models import ContractExpense  # noqa: F401
from pigrocrm.core.contracts.models import (  # noqa: F401
    ClauseNote,
    Contract,
    RateCard,
    RenewalAssumption,
)
from pigrocrm.core.deals.models import Deal  # noqa: F401
from pigrocrm.core.digest.models import Digest  # noqa: F401
from pigrocrm.core.documents.models import Document, DocumentVersion  # noqa: F401
from pigrocrm.core.drive.models import GoogleDriveAccount  # noqa: F401
from pigrocrm.core.due_items.models import DueItem, DueItemSettlement  # noqa: F401
from pigrocrm.core.expense_notes.models import ExpenseNote  # noqa: F401
from pigrocrm.core.fields.models import FieldDefinition  # noqa: F401
from pigrocrm.core.fiscal.f24_models import (  # noqa: F401
    F24Group,
    F24GroupItem,
    F24GroupReceipt,
    TaxCredit,
)
from pigrocrm.core.fiscal.models import (  # noqa: F401
    DeclaredTaxFigure,
    FiscalEstimateReading,
    FiscalProfile,
    IvaPeriodLock,
    IvaSettlement,
    TaxAdvanceMethod,
    TaxInstalmentPlan,
    TaxLoss,
    TaxNoticePlan,
)
from pigrocrm.core.gmail.models import (  # noqa: F401
    EmailDraft,
    GmailKnownAddress,
    GmailMessage,
    GmailMessageLink,
    GoogleAccount,
    GoogleOAuthState,
    PaymentReminder,
)
from pigrocrm.core.inbox.models import InboxItem  # noqa: F401
from pigrocrm.core.invoices.models import (  # noqa: F401
    Invoice,
    InvoiceCounter,
    InvoiceLine,
    InvoiceRegisterGap,
)
from pigrocrm.core.legal_entities.models import LegalEntity, LegalEntitySite  # noqa: F401
from pigrocrm.core.mailboxes.models import Mailbox, MailboxPurpose  # noqa: F401
from pigrocrm.core.notes.models import Note, NoteRevision  # noqa: F401
from pigrocrm.core.organizations.models import Organization  # noqa: F401
from pigrocrm.core.people.models import Person  # noqa: F401
from pigrocrm.core.pipeline.models import PipelineStage  # noqa: F401
from pigrocrm.core.proposals.models import Proposal  # noqa: F401
from pigrocrm.core.purchases.models import Purchase, PurchaseVatLine  # noqa: F401
from pigrocrm.core.reminders.models import (  # noqa: F401
    ReminderAck,
    ReminderDelivery,
    ReminderJobRun,
    ReminderPreference,
    ReminderSnooze,
    ReminderSummaryRun,
)
from pigrocrm.core.reminders.push_subscriptions import PushSubscription  # noqa: F401
from pigrocrm.core.slack.models import (  # noqa: F401
    SlackChannelPost,
    SlackUserLink,
    SlackWorkspace,
)
from pigrocrm.core.space_settings.models import SpaceSetting  # noqa: F401
from pigrocrm.core.staff.models import PayrollSettings, StaffMember  # noqa: F401
from pigrocrm.core.tasks.models import Task  # noqa: F401
from pigrocrm.core.templates.models import Template  # noqa: F401
from pigrocrm.core.timetracking.models import (  # noqa: F401
    CostCategory,
    PeriodLock,
    TimeEntry,
)
from pigrocrm.core.work_units.models import (  # noqa: F401
    Approval,
    WorkUnit,
    WorkUnitTransition,
)
