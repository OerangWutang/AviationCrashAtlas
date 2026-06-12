"""ORM model facade.

All model classes now live in the ``_orm_*.py`` sub-modules alongside this
file.  This module imports each sub-module so that SQLAlchemy's
``Base.metadata`` is fully populated, then re-exports every public name so
that the ~30 existing ``from atlas.infrastructure.db.orm_models import XModel``
call-sites continue to work without any changes.
"""

# ruff: noqa: F401  (all names re-exported for backward compat)

from atlas.infrastructure.db._orm_base import Base, ComplianceMixin, gen_uuid, now_utc

# Sub-module imports populate Base.metadata as a side-effect.
from atlas.infrastructure.db._orm_core import (
    AccidentEventModel,
    ClaimConflictClaimModel,
    ClaimConflictModel,
    ClaimHistoryModel,
    ClaimModel,
    ConflictActivityLogModel,
    IngestionRunModel,
    RawSnapshotModel,
    SourceModel,
)
from atlas.infrastructure.db._orm_projections import (
    AccidentProjectionHistoryModel,
    ArchiveManifestModel,
    OutboxEventModel,
    OutboxWorkerHeartbeatModel,
    ProjectedAccidentRecordModel,
    _OUTBOX_EVENT_TYPES,
)
from atlas.infrastructure.db._orm_identity import (
    ApiKeyAttemptModel,
    ApiKeyModel,
    EventIdentityIndexModel,
    PendingDuplicateReviewModel,
)
from atlas.infrastructure.db._orm_orion import (
    OrionEntityClaimLinkModel,
    OrionEntityIdentifierModel,
    OrionEntityModel,
    OrionEntityReviewModel,
    OrionRelationshipModel,
    _ORION_ENTITY_TYPES,
    _ORION_REL_TYPES,
    _ORION_REVIEW_STATUSES,
)
from atlas.infrastructure.db._orm_chronos import (
    ChronosEventLinkModel,
    ChronosSequenceReviewModel,
    ChronosTimelineEventModel,
    _CHRONOS_EVENT_TYPES,
    _CHRONOS_PRECISIONS,
    _CHRONOS_REVIEW_STATUSES,
)
from atlas.infrastructure.db._orm_hermes import (
    HermesCrawlTargetModel,
    HermesFetchedDocumentModel,
    HermesFetchJobModel,
    HermesSourceChangeModel,
    HermesSourceModel,
)
from atlas.infrastructure.db._orm_argus import (
    ArgusSignalEvidenceModel,
    ArgusSignalModel,
    ArgusSignalReviewModel,
    _ARGUS_DECISIONS,
    _ARGUS_EVIDENCE_TYPES,
    _ARGUS_SEVERITIES,
    _ARGUS_SIGNAL_TYPES,
    _ARGUS_STATUSES,
)
from atlas.infrastructure.db._orm_publication import (
    MapIndexEntryModel,
    PublicEventPageModel,
    PublicEventPageRevisionModel,
    SearchIndexEntryModel,
)
from atlas.infrastructure.db._orm_tenancy import (
    TenantClaimModel,
    TenantCrossrefResultModel,
    TenantEventAssociationModel,
    TenantEventOverlayModel,
    TenantIngestionRunModel,
    TenantMembershipModel,
    TenantModel,
    TenantSafetyReportModel,
    TenantSourceModel,
)
from atlas.infrastructure.db._orm_cms import (
    ChangelogEntryModel,
    ChangelogEntryRevisionModel,
    GlossaryTermModel,
    GlossaryTermRevisionModel,
    MethodologyPageModel,
    MethodologyPageRevisionModel,
)
from atlas.infrastructure.db._orm_analysis import (
    EventHfacsAttributionModel,
    HfacsCategoryModel,
    HfacsSubcategoryModel,
    NlQueryLogModel,
    SavedNlQueryModel,
    SheloFactorInteractionModel,
    SheloFactorModel,
    UsageDailyRollupModel,
    UsageEventModel,
)
from atlas.infrastructure.db._orm_documents import (
    ComplianceEventModel,
    ReportExportModel,
    UploadedDocumentModel,
)

__all__ = [
    # base
    "Base",
    "ComplianceMixin",
    "gen_uuid",
    "now_utc",
    # core
    "SourceModel",
    "IngestionRunModel",
    "RawSnapshotModel",
    "AccidentEventModel",
    "ClaimModel",
    "ClaimHistoryModel",
    "ClaimConflictModel",
    "ClaimConflictClaimModel",
    "ConflictActivityLogModel",
    # projections
    "ProjectedAccidentRecordModel",
    "OutboxEventModel",
    "OutboxWorkerHeartbeatModel",
    "AccidentProjectionHistoryModel",
    "ArchiveManifestModel",
    "_OUTBOX_EVENT_TYPES",
    # identity
    "ApiKeyModel",
    "ApiKeyAttemptModel",
    "PendingDuplicateReviewModel",
    "EventIdentityIndexModel",
    # orion
    "OrionEntityModel",
    "OrionEntityIdentifierModel",
    "OrionRelationshipModel",
    "OrionEntityClaimLinkModel",
    "OrionEntityReviewModel",
    "_ORION_ENTITY_TYPES",
    "_ORION_REL_TYPES",
    "_ORION_REVIEW_STATUSES",
    # chronos
    "ChronosTimelineEventModel",
    "ChronosEventLinkModel",
    "ChronosSequenceReviewModel",
    "_CHRONOS_EVENT_TYPES",
    "_CHRONOS_PRECISIONS",
    "_CHRONOS_REVIEW_STATUSES",
    # hermes
    "HermesSourceModel",
    "HermesCrawlTargetModel",
    "HermesFetchJobModel",
    "HermesFetchedDocumentModel",
    "HermesSourceChangeModel",
    # argus
    "ArgusSignalModel",
    "ArgusSignalEvidenceModel",
    "ArgusSignalReviewModel",
    "_ARGUS_SIGNAL_TYPES",
    "_ARGUS_STATUSES",
    "_ARGUS_SEVERITIES",
    "_ARGUS_EVIDENCE_TYPES",
    "_ARGUS_DECISIONS",
    # publication
    "PublicEventPageModel",
    "PublicEventPageRevisionModel",
    "SearchIndexEntryModel",
    "MapIndexEntryModel",
    # tenancy
    "TenantModel",
    "TenantMembershipModel",
    "TenantSourceModel",
    "TenantIngestionRunModel",
    "TenantClaimModel",
    "TenantEventOverlayModel",
    "TenantSafetyReportModel",
    "TenantEventAssociationModel",
    "TenantCrossrefResultModel",
    # cms
    "GlossaryTermModel",
    "GlossaryTermRevisionModel",
    "MethodologyPageModel",
    "MethodologyPageRevisionModel",
    "ChangelogEntryModel",
    "ChangelogEntryRevisionModel",
    # analysis
    "HfacsCategoryModel",
    "HfacsSubcategoryModel",
    "EventHfacsAttributionModel",
    "SheloFactorModel",
    "SheloFactorInteractionModel",
    "NlQueryLogModel",
    "SavedNlQueryModel",
    "UsageEventModel",
    "UsageDailyRollupModel",
    # documents
    "UploadedDocumentModel",
    "ReportExportModel",
    "ComplianceEventModel",
]
