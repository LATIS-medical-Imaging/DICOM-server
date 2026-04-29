"""DICOM ingestion — parse a .dcm file and upsert the Patient/Study/Series/Instance hierarchy."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any

import numpy as np
import pydicom
from PIL import Image
from pydicom.dataset import Dataset
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models.instance import Instance
from app.db.models.patient import Patient
from app.db.models.series import Series
from app.db.models.study import Study, StudyStatus
from app.services.storage_service import StorageService

logger = get_logger(__name__)


class IngestionService:
    def __init__(self, db: Session, storage: StorageService, settings: Settings) -> None:
        self._db = db
        self._storage = storage
        self._settings = settings

    def ingest(self, object_key: str, owner_id: str, file_size_bytes: int) -> uuid.UUID:
        """Orchestrates the full ingestion pipeline. Returns the study UUID."""
        ds = self._download_and_parse(object_key)

        patient = self._upsert_patient(ds, uuid.UUID(owner_id))
        study, _ = self._upsert_study(ds, uuid.UUID(owner_id), patient.id)
        series, series_created = self._upsert_series(ds, study.id)
        instance = self._insert_instance_if_new(ds, series.id, object_key, file_size_bytes)

        if instance is not None:
            self._increment_counters(series.id, study.id, file_size_bytes, series_created)

        self._generate_thumbnail(ds, object_key)
        return study.id

    def _download_and_parse(self, object_key: str) -> Dataset:
        response = self._storage.client.get_object(self._settings.minio_bucket_dicom, object_key)
        try:
            dcm_bytes = response.read()
        finally:
            response.close()
            response.release_conn()
        return pydicom.dcmread(BytesIO(dcm_bytes))

    def _upsert_patient(self, ds: Dataset, owner_id: uuid.UUID) -> Patient:
        patient_id_tag = _tag(ds, "PatientID") or "UNKNOWN"
        patient = self._db.execute(
            select(Patient).where(
                Patient.patient_id == patient_id_tag,
                Patient.created_by == owner_id,
            )
        ).scalar_one_or_none()

        if patient is None:
            raw_sex = _tag(ds, "PatientSex", "O").upper()[:1]
            sex = raw_sex if raw_sex in ("M", "F", "O") else "O"
            patient = Patient(
                created_by=owner_id,
                patient_name=_tag(ds, "PatientName", "Unknown"),
                patient_id=patient_id_tag,
                birth_date=_parse_date(_tag(ds, "PatientBirthDate")) or date(1900, 1, 1),
                sex=sex,
            )
            self._db.add(patient)
            self._db.flush()
            logger.info("patient_created", patient_id=patient_id_tag)

        return patient

    def _upsert_study(
        self,
        ds: Dataset,
        owner_id: uuid.UUID,
        patient_id: uuid.UUID,
    ) -> tuple[Study, bool]:
        """Returns (study, created)."""
        study_uid = str(ds.StudyInstanceUID)
        study = self._db.execute(
            select(Study).where(Study.study_instance_uid == study_uid)
        ).scalar_one_or_none()

        if study is None:
            study = Study(
                owner_id=owner_id,
                patient_id=patient_id,
                study_instance_uid=study_uid,
                accession_number=_tag(ds, "AccessionNumber") or None,
                study_date=_parse_date(_tag(ds, "StudyDate")),
                study_time=_parse_time(_tag(ds, "StudyTime")),
                study_description=_tag(ds, "StudyDescription") or None,
                modality=_tag(ds, "Modality") or None,
                referring_physician=_tag(ds, "ReferringPhysicianName") or None,
                institution_name=_tag(ds, "InstitutionName") or None,
                storage_path=f"{owner_id}/{study_uid}/",
                status=StudyStatus.PROCESSING,
            )
            self._db.add(study)
            self._db.flush()
            logger.info("study_created", study_uid=study_uid)
            return study, True

        return study, False

    def _upsert_series(self, ds: Dataset, study_id: uuid.UUID) -> tuple[Series, bool]:
        """Returns (series, created)."""
        series_uid = str(ds.SeriesInstanceUID)
        series = self._db.execute(
            select(Series).where(Series.series_instance_uid == series_uid)
        ).scalar_one_or_none()

        if series is None:
            ps = getattr(ds, "PixelSpacing", None)
            pixel_spacing = "\\".join(str(v) for v in ps) if ps else None

            series = Series(
                study_id=study_id,
                series_instance_uid=series_uid,
                series_number=int(getattr(ds, "SeriesNumber", 0)) or None,
                modality=_tag(ds, "Modality", "OT"),
                series_description=_tag(ds, "SeriesDescription") or None,
                body_part_examined=_tag(ds, "BodyPartExamined") or None,
                patient_position=_tag(ds, "PatientPosition") or None,
                protocol_name=_tag(ds, "ProtocolName") or None,
                slice_thickness=_decimal_tag(ds, "SliceThickness"),
                spacing_between_slices=_decimal_tag(ds, "SpacingBetweenSlices"),
                pixel_spacing=pixel_spacing,
                storage_path=f"{study_id}/{series_uid}/",
            )
            self._db.add(series)
            self._db.flush()
            logger.info("series_created", series_uid=series_uid)
            return series, True

        return series, False

    def _insert_instance_if_new(
        self,
        ds: Dataset,
        series_id: uuid.UUID,
        object_key: str,
        file_size_bytes: int,
    ) -> Instance | None:
        """Returns None if the SOPInstanceUID already exists (idempotent re-delivery)."""
        sop_uid = str(ds.SOPInstanceUID)
        existing = self._db.execute(
            select(Instance.id).where(Instance.sop_instance_uid == sop_uid)
        ).scalar_one_or_none()
        if existing is not None:
            logger.info("instance_already_exists", sop_uid=sop_uid)
            return None

        ipp = getattr(ds, "ImagePositionPatient", None)
        iop = getattr(ds, "ImageOrientationPatient", None)
        transfer_syntax: str | None = None
        if hasattr(ds, "file_meta") and hasattr(ds.file_meta, "TransferSyntaxUID"):
            transfer_syntax = str(ds.file_meta.TransferSyntaxUID)

        instance = Instance(
            series_id=series_id,
            sop_instance_uid=sop_uid,
            sop_class_uid=_tag(ds, "SOPClassUID") or None,
            instance_number=int(getattr(ds, "InstanceNumber", 0)) or None,
            rows=int(getattr(ds, "Rows", 0)) or None,
            columns=int(getattr(ds, "Columns", 0)) or None,
            bits_allocated=int(getattr(ds, "BitsAllocated", 0)) or None,
            bits_stored=int(getattr(ds, "BitsStored", 0)) or None,
            pixel_representation=int(getattr(ds, "PixelRepresentation", 0)),
            number_of_frames=int(getattr(ds, "NumberOfFrames", 1)) or 1,
            window_center=_decimal_tag(ds, "WindowCenter"),
            window_width=_decimal_tag(ds, "WindowWidth"),
            rescale_intercept=_decimal_tag(ds, "RescaleIntercept"),
            rescale_slope=_decimal_tag(ds, "RescaleSlope"),
            image_position_patient="\\".join(str(v) for v in ipp) if ipp else None,
            image_orientation_patient="\\".join(str(v) for v in iop) if iop else None,
            transfer_syntax_uid=transfer_syntax,
            file_path=object_key,
            file_size_bytes=file_size_bytes,
        )
        self._db.add(instance)
        self._db.flush()
        return instance

    def _increment_counters(
        self,
        series_id: uuid.UUID,
        study_id: uuid.UUID,
        file_size_bytes: int,
        new_series: bool,
    ) -> None:
        self._db.execute(
            update(Series)
            .where(Series.id == series_id)
            .values(
                instance_count=Series.instance_count + 1,
                size_bytes=Series.size_bytes + file_size_bytes,
            )
        )
        study_values: dict[str, Any] = {
            "total_instance_count": Study.total_instance_count + 1,
            "total_size_bytes": Study.total_size_bytes + file_size_bytes,
        }
        if new_series:
            study_values["total_series_count"] = Study.total_series_count + 1
        self._db.execute(update(Study).where(Study.id == study_id).values(**study_values))

    def _generate_thumbnail(self, ds: Dataset, object_key: str) -> None:
        # Non-fatal — a missing thumbnail only affects the study list preview.
        try:
            arr = ds.pixel_array  # raises if no pixel data or unsupported transfer syntax
            if arr.ndim == 3:
                arr = arr[0]

            arr = arr.astype(np.float32)
            lo, hi = float(arr.min()), float(arr.max())
            if hi > lo:
                arr = (arr - lo) / (hi - lo) * 255.0

            img = Image.fromarray(arr.astype(np.uint8))
            img.thumbnail((256, 256), Image.Resampling.LANCZOS)

            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85)
            length = buf.tell()
            buf.seek(0)

            thumb_key = object_key.rsplit(".", 1)[0] + ".jpg"
            self._storage.put_object(
                self._settings.minio_bucket_thumbnails,
                thumb_key,
                buf,
                length=length,
                content_type="image/jpeg",
            )
            logger.info("thumbnail_stored", thumb_key=thumb_key)

        except Exception as exc:
            logger.warning("thumbnail_generation_failed", object_key=object_key, error=str(exc))


def _tag(ds: Dataset, name: str, default: str = "") -> str:
    val = getattr(ds, name, default)
    return str(val).strip() if val is not None else default


def _decimal_tag(ds: Dataset, name: str) -> Decimal | None:
    val = getattr(ds, name, None)
    if val is None:
        return None
    try:
        # Some tags (e.g. WindowCenter) are multi-valued sequences; take the first.
        if hasattr(val, "__iter__") and not isinstance(val, str):
            val = next(iter(val))
        # Convert via string to avoid float precision loss on DICOM DSfloat values.
        return Decimal(str(float(val)))
    except (InvalidOperation, ValueError, StopIteration):
        return None


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _parse_time(value: str) -> time | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:6], "%H%M%S").time()
    except ValueError:
        return None
