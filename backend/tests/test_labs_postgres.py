from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.lab_contracts import LAB_ANALYTE_GUIDE_PROMPT_VERSION
from app.lab_models import (
    LabAnalyte,
    LabAnalyteGuide,
    LabDocument,
    LabReport,
    LabResult,
    StoredFile,
)
from app.labs import missing_analyte_guides


POSTGRES_URL = os.environ.get("AMIGO_TEST_POSTGRES_URL")


@pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL regression database is not configured")
def test_missing_analyte_guides_deduplicates_without_comparing_json_aliases():
    engine = create_engine(POSTGRES_URL, future=True)
    schema = f"test_{uuid4().hex}"
    tables = [
        StoredFile.__table__,
        LabDocument.__table__,
        LabReport.__table__,
        LabAnalyte.__table__,
        LabAnalyteGuide.__table__,
        LabResult.__table__,
    ]
    now = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        with engine.connect().execution_options(
            schema_translate_map={None: schema}
        ) as connection:
            LabDocument.metadata.create_all(connection, tables=tables)
            with Session(connection, expire_on_commit=False) as db:
                document = LabDocument(
                    id=str(uuid4()),
                    storage_key="postgres-regression.bin",
                    original_filename="postgres-regression.pdf",
                    file_sha256="a" * 64,
                    media_type="application/pdf",
                    size_bytes=10,
                    status="processing",
                    processing_stage="extracting",
                    progress_percent=85,
                    verified=False,
                    created_at=now,
                    updated_at=now,
                )
                analytes = [
                    LabAnalyte(
                        id="custom-json-marker",
                        display_name="JSON marker",
                        aliases=["json alias", "second alias"],
                    ),
                    LabAnalyte(
                        id="custom-persisted-guide",
                        display_name="Persisted marker",
                        aliases=["persisted alias"],
                    ),
                    LabAnalyte(
                        id="custom-deleted-only",
                        display_name="Deleted marker",
                        aliases=["deleted alias"],
                    ),
                    LabAnalyte(
                        id="glucose",
                        display_name="Глюкоза",
                        aliases=["glucose alias"],
                    ),
                ]
                db.add(document)
                db.add_all(analytes)
                db.flush()
                db.add(
                    LabAnalyteGuide(
                        analyte_id="custom-persisted-guide",
                        summary="Persisted guide summary",
                        why_tested="Persisted guide purpose",
                        low_meaning="Persisted low meaning",
                        high_meaning="Persisted high meaning",
                        contract_version=LAB_ANALYTE_GUIDE_PROMPT_VERSION,
                        model="gpt-5.6-sol",
                        created_at=now,
                        updated_at=now,
                    )
                )
                for index, analyte_id in enumerate(
                    [
                        "custom-json-marker",
                        "custom-json-marker",
                        "custom-persisted-guide",
                        "custom-deleted-only",
                        "glucose",
                    ]
                ):
                    db.add(
                        LabResult(
                            id=str(uuid4()),
                            document_id=document.id,
                            analyte_id=analyte_id,
                            source_index=index,
                            analyte_name=analyte_id,
                            value_numeric=Decimal(index + 1),
                            reference_source="none",
                            status="indeterminate",
                            verification_status="unverified",
                            deleted=analyte_id == "custom-deleted-only",
                            created_at=now,
                            updated_at=now,
                        )
                    )
                db.commit()

                missing = missing_analyte_guides(db, document_id=document.id)

                assert [analyte.id for analyte in missing] == ["custom-json-marker"]
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
