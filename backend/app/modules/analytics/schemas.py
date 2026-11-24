from pydantic import BaseModel


class Summary(BaseModel):
    """Headline counts for the dashboard, computed from the caller's own records."""

    total_contacts: int
    emails_sent: int
    replies_received: int
    positive_replies: int
    interviews: int
    rejections: int
    pending_drafts: int
    follow_ups_due: int
