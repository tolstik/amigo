from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import os

os.environ.setdefault("AMIGO_DATABASE_URL", "sqlite+pysqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Measurement, MeasurementGroup


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    Base.metadata.drop_all(engine)


@pytest.fixture
def add_group(db: Session):
    def add(
        provider_id: str,
        measured_at: datetime,
        values: dict[str, tuple[float, str]],
        provider: str = "test",
    ) -> MeasurementGroup:
        group = MeasurementGroup(
            provider=provider,
            provider_group_id=provider_id,
            measured_at=measured_at,
            source=provider,
            raw_payload={},
        )
        db.add(group)
        db.flush()
        for kind, (value, unit) in values.items():
            db.add(
                Measurement(
                    group_id=group.id,
                    kind=kind,
                    value=Decimal(str(value)),
                    unit=unit,
                )
            )
        db.commit()
        return group

    return add


@pytest.fixture
def utc():
    return timezone.utc
