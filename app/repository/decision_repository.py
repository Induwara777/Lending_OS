"""Storage. Decisions are append-only (DEC-04): there is no update or delete path, and the ORM refuses one."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    event,
    select,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.models import Decision, Referral, ReferralStatus


class ReferralAlreadyResolved(Exception):
    pass


class Base(DeclarativeBase):
    pass


class DecisionRow(Base):
    __tablename__ = "decisions"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_decision_dedupe"),)

    decision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    application_id: Mapped[str] = mapped_column(String(36), index=True)
    outcome: Mapped[str] = mapped_column(String(10))
    decided_by: Mapped[str] = mapped_column(String(64))
    parent_decision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Set only for system decisions: application + features hash + rule set. Makes redelivery harmless.
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)  # the full Decision, stored as decided


class ReferralRow(Base):
    __tablename__ = "referrals"

    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.decision_id"), primary_key=True)
    application_id: Mapped[str] = mapped_column(String(36), index=True)
    requested_amount: Mapped[int] = mapped_column(Integer)
    assigned_role: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10), index=True)
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_decision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@event.listens_for(DecisionRow, "before_update")
def _no_update(*_args):
    raise RuntimeError("decisions are append-only and can never be updated")


@event.listens_for(DecisionRow, "before_delete")
def _no_delete(*_args):
    raise RuntimeError("decisions are append-only and can never be deleted")


def make_session_factory(database_url: str) -> sessionmaker[Session]:
    kwargs = {}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url:
            kwargs["poolclass"] = StaticPool
    engine = create_engine(database_url, **kwargs)
    Base.metadata.create_all(engine)  # swap for Alembic migrations before production
    return sessionmaker(engine, expire_on_commit=False)


def dedupe_key(application_id: UUID, features_hash: str, rule_set_version: str) -> str:
    return f"{application_id}:{features_hash}:{rule_set_version}"


class DecisionRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._sf = session_factory

    # --- decisions ---------------------------------------------------------
    def add(self, decision: Decision, referral: Referral | None = None) -> None:
        """Insert the decision (and its referral row, if any) in one transaction."""
        key = (
            dedupe_key(decision.application_id, decision.features_hash, decision.rule_set_version)
            if decision.decided_by == "system"
            else None
        )
        with self._sf.begin() as s:
            s.add(
                DecisionRow(
                    decision_id=str(decision.decision_id),
                    application_id=str(decision.application_id),
                    outcome=decision.outcome.value,
                    decided_by=decision.decided_by,
                    parent_decision_id=str(decision.parent_decision_id) if decision.parent_decision_id else None,
                    dedupe_key=key,
                    decided_at=decision.decided_at,
                    payload=decision.model_dump(mode="json"),
                )
            )
            if referral:
                s.flush()
                s.add(_referral_row(referral))

    def get(self, decision_id: UUID) -> Decision | None:
        with self._sf() as s:
            row = s.get(DecisionRow, str(decision_id))
            return Decision.model_validate(row.payload) if row else None

    def list_for_application(self, application_id: UUID) -> list[Decision]:
        with self._sf() as s:
            rows = s.scalars(
                select(DecisionRow)
                .where(DecisionRow.application_id == str(application_id))
                .order_by(DecisionRow.decided_at)
            )
            return [Decision.model_validate(r.payload) for r in rows]

    def find_system_decision(self, application_id: UUID, features_hash: str, rule_set_version: str) -> Decision | None:
        key = dedupe_key(application_id, features_hash, rule_set_version)
        with self._sf() as s:
            row = s.scalar(select(DecisionRow).where(DecisionRow.dedupe_key == key))
            return Decision.model_validate(row.payload) if row else None

    # --- referrals (DEC-06) ---------------------------------------------------
    def get_referral(self, decision_id: UUID) -> Referral | None:
        with self._sf() as s:
            row = s.get(ReferralRow, str(decision_id))
            return _referral(row) if row else None

    def list_open_referrals(self, max_amount: int | None = None) -> list[Referral]:
        """Open referrals, optionally only those whose amount is within `max_amount` (the caller's approval limit)."""
        with self._sf() as s:
            q = select(ReferralRow).where(ReferralRow.status == ReferralStatus.OPEN.value)
            if max_amount is not None:
                q = q.where(ReferralRow.requested_amount <= max_amount)
            rows = s.scalars(q.order_by(ReferralRow.created_at))
            return [_referral(r) for r in rows]

    def add_officer_decision(self, decision: Decision, referral_id: UUID) -> None:
        """Close an OPEN referral and store the officer's decision in ONE transaction.

        Raises ReferralAlreadyResolved if someone else got there first, so two officers can't both decide.
        """
        with self._sf.begin() as s:
            result = s.execute(
                update(ReferralRow)
                .where(ReferralRow.decision_id == str(referral_id), ReferralRow.status == ReferralStatus.OPEN.value)
                .values(
                    status=ReferralStatus.RESOLVED.value,
                    resolved_by=decision.decided_by,
                    resolved_decision_id=str(decision.decision_id),
                )
            )
            if result.rowcount != 1:
                raise ReferralAlreadyResolved(str(referral_id))
            s.add(
                DecisionRow(
                    decision_id=str(decision.decision_id),
                    application_id=str(decision.application_id),
                    outcome=decision.outcome.value,
                    decided_by=decision.decided_by,
                    parent_decision_id=str(decision.parent_decision_id) if decision.parent_decision_id else None,
                    dedupe_key=None,
                    decided_at=decision.decided_at,
                    payload=decision.model_dump(mode="json"),
                )
            )


def _referral_row(r: Referral) -> ReferralRow:
    return ReferralRow(
        decision_id=str(r.decision_id),
        application_id=str(r.application_id),
        requested_amount=r.requested_amount,
        assigned_role=r.assigned_role,
        status=r.status.value,
        resolved_by=r.resolved_by,
        resolved_decision_id=str(r.resolved_decision_id) if r.resolved_decision_id else None,
        created_at=r.created_at,
    )


def _referral(row: ReferralRow) -> Referral:
    return Referral(
        decision_id=row.decision_id,
        application_id=row.application_id,
        requested_amount=row.requested_amount,
        assigned_role=row.assigned_role,
        status=row.status,
        resolved_by=row.resolved_by,
        resolved_decision_id=row.resolved_decision_id,
        created_at=row.created_at,
    )
