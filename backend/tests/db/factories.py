from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models import (
    ActivityLog,
    Application,
    Attachment,
    CandidateFact,
    CandidateProfile,
    Company,
    CompanyResearch,
    Contact,
    ContactSource,
    Conversation,
    EmailDraft,
    EmailMessage,
    EmailVerification,
    FollowUpTask,
    IntegrationSecret,
    Opportunity,
    OutreachCampaign,
    Resume,
    ResumeSkill,
    SuppressionEntry,
    UserIntegration,
    UserSettings,
)

# Every table an owner reads and writes through RLS.
OWNED_MODELS: tuple[type[Any], ...] = (
    CandidateProfile,
    CandidateFact,
    UserSettings,
    Resume,
    ResumeSkill,
    Company,
    CompanyResearch,
    Contact,
    ContactSource,
    EmailVerification,
    Opportunity,
    Application,
    OutreachCampaign,
    Conversation,
    EmailDraft,
    EmailMessage,
    FollowUpTask,
    SuppressionEntry,
    Attachment,
    UserIntegration,
)
# Plus the audit log, which is readable but append-only.
RLS_VISIBLE_MODELS: tuple[type[Any], ...] = (*OWNED_MODELS, ActivityLog)
# Plus stored tokens, which no client-facing role can read at all.
ALL_USER_MODELS: tuple[type[Any], ...] = (*RLS_VISIBLE_MODELS, IntegrationSecret)


def rows_changed(result: Any) -> int:
    return int(result.rowcount)


def now() -> datetime:
    return datetime.now(UTC)


def unique_email() -> str:
    return f"{uuid4()}@example.test"


def add[T](session: Session, obj: T) -> T:
    session.add(obj)
    session.flush()
    return obj


def company(session: Session, user_id: UUID, **fields: Any) -> Company:
    return add(session, Company(user_id=user_id, **{"name": "Acme", **fields}))


def contact(session: Session, user_id: UUID, **fields: Any) -> Contact:
    return add(session, Contact(user_id=user_id, **{"full_name": "Grace Hopper", **fields}))


def conversation(session: Session, user_id: UUID, contact_id: UUID, **fields: Any) -> Conversation:
    return add(session, Conversation(user_id=user_id, contact_id=contact_id, **fields))


def draft(session: Session, user_id: UUID, contact_id: UUID, **fields: Any) -> EmailDraft:
    defaults = {"subject": "Internship question", "body": "Hello"}
    return add(session, EmailDraft(user_id=user_id, contact_id=contact_id, **defaults | fields))


def approve(session: Session, row: EmailDraft) -> None:
    """What the review queue will do when the user approves the text they see."""
    session.refresh(row)
    row.status = "approved"
    row.approved_at = now()
    row.approved_version = row.version
    session.flush()


def sending_draft(session: Session, user_id: UUID, contact_id: UUID, **fields: Any) -> EmailDraft:
    row = draft(session, user_id, contact_id, **fields)
    approve(session, row)
    row.status = "sending"
    session.flush()
    return row


def outbound_message(
    session: Session, user_id: UUID, conversation_id: UUID, draft_id: UUID | None
) -> EmailMessage:
    return add(
        session,
        EmailMessage(
            user_id=user_id,
            conversation_id=conversation_id,
            draft_id=draft_id,
            direction="outbound",
            from_address="me@example.test",
            to_addresses=["them@example.test"],
        ),
    )


def seed_everything(session: Session, user_id: UUID) -> None:
    """One row in every user-owned table, wired together the way the product will use them."""
    profile = add(session, CandidateProfile(user_id=user_id))
    add(session, CandidateFact(user_id=user_id, profile_id=profile.id, fact="Built a compiler"))
    add(session, UserSettings(user_id=user_id))
    resume = add(session, Resume(user_id=user_id, name="Main"))
    add(session, ResumeSkill(user_id=user_id, resume_id=resume.id, skill="Python"))
    company_row = company(session, user_id)
    add(session, CompanyResearch(user_id=user_id, company_id=company_row.id, provider="manual"))
    contact_row = contact(session, user_id, company_id=company_row.id, email=unique_email())
    add(session, ContactSource(user_id=user_id, contact_id=contact_row.id, provider="manual"))
    add(
        session,
        EmailVerification(
            user_id=user_id,
            contact_id=contact_row.id,
            email=unique_email(),
            provider="test",
            status="valid",
        ),
    )
    opportunity = add(
        session, Opportunity(user_id=user_id, company_id=company_row.id, title="Intern")
    )
    add(session, Application(user_id=user_id, opportunity_id=opportunity.id, resume_id=resume.id))
    campaign = add(
        session,
        OutreachCampaign(
            user_id=user_id, name="Spring", resume_id=resume.id, opportunity_id=opportunity.id
        ),
    )
    conversation_row = conversation(session, user_id, contact_row.id, campaign_id=campaign.id)
    draft_row = sending_draft(
        session,
        user_id,
        contact_row.id,
        campaign_id=campaign.id,
        conversation_id=conversation_row.id,
        opportunity_id=opportunity.id,
        resume_id=resume.id,
    )
    outbound_message(session, user_id, conversation_row.id, draft_row.id)
    add(
        session,
        FollowUpTask(
            user_id=user_id,
            conversation_id=conversation_row.id,
            contact_id=contact_row.id,
            due_at=now(),
        ),
    )
    add(session, SuppressionEntry(user_id=user_id, email=unique_email()))
    add(
        session,
        Attachment(
            user_id=user_id,
            draft_id=draft_row.id,
            resume_id=resume.id,
            kind="resume",
            filename="cv.pdf",
            mime_type="application/pdf",
            storage_path=f"{user_id}/cv.pdf",
        ),
    )
    integration = add(
        session, UserIntegration(user_id=user_id, provider="gmail", kind="email_delivery")
    )
    add(
        session,
        IntegrationSecret(
            user_id=user_id, integration_id=integration.id, encrypted_payload=b"ciphertext"
        ),
    )
    add(session, ActivityLog(user_id=user_id, action="test.seeded"))
