"""Series Phases — business logic.

A *phase* is a Series row whose ``parent_series_id`` is set.  It owns Instance
rows for every slice the doctor "touched" (filter applied OR annotation drawn)
plus the annotations themselves.  Untouched slices are served from the parent
series at render time via :meth:`PhaseService.list_instances_rendered`.

Phases are private to their creator (``owner_id``) — even a colleague with view
access to the parent study sees only the original series, not anyone else's
phases.

In-place save (PATCH) is delete-then-insert wrapped in a single transaction:
the snapshot model means "what's in the phase right now" is exactly what the
last save said it was — no history kept.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

import pydicom.uid
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.db.models.annotation import Annotation
from app.db.models.instance import Instance
from app.db.models.series import Series
from app.schemas.phases import (
    AnnotationPayload,
    CreatePhaseRequest,
    PhaseInstancePayload,
    UpdatePhaseRequest,
)
from app.services.storage_service import StorageService
from app.services.study_service import StudyService


class PhaseService:
    """All read/write paths for the phases feature.

    Constructed per-request — the FastAPI dependency layer wires in the async
    session and the storage service.  ``StudyService`` is used for the
    parent-study visibility check (so phase queries inherit the same
    owner-or-share access rules as the rest of the metadata API).
    """

    def __init__(
        self,
        db: AsyncSession,
        storage: StorageService,
        settings: Settings,
    ) -> None:
        self._db = db
        self._storage = storage
        self._settings = settings
        self._studies = StudyService(db)

    # ── Read paths ──────────────────────────────────────────────────────

    async def list_for_series(
        self,
        parent_series_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> list[Series]:
        """List the caller's phases for a given parent series, newest first.

        The parent series must itself be visible to the caller (study owner or
        active share).  Phases are then filtered to ``owner_id == user_id`` —
        a stricter rule than parent visibility, matching the "private to
        creator" policy.
        """
        parent = await self._load_parent_visible(parent_series_id, user_id)
        result = await self._db.execute(
            select(Series)
            .where(
                Series.parent_series_id == parent.id,
                Series.owner_id == user_id,
            )
            .order_by(Series.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_phase(self, phase_id: uuid.UUID, user_id: uuid.UUID) -> Series:
        """Load a phase the caller owns.  404 on miss or foreign owner."""
        phase = await self._load_phase(phase_id)
        if phase.owner_id != user_id:
            # Don't leak existence to non-owners — same shape as study visibility.
            raise NotFoundError("Phase not found.")
        return phase

    async def list_instances_rendered(self, series: Series) -> list[Instance]:
        """Return the ordered instance stack as the viewer should display it.

        * For an original series (no ``parent_series_id``): returns its own
          instances ordered by ``instance_number`` — same behaviour as
          ``StudyService.list_instances`` so the existing studies endpoint
          can route through this helper without behaviour change.
        * For a phase: returns the parent's instances with the phase's
          overrides spliced in by ``instance_number``.
        """
        if series.parent_series_id is None:
            return await self._studies.list_instances(series.id)

        # Phase — merge parent + phase overrides.
        parent_instances = await self._studies.list_instances(series.parent_series_id)
        phase_instances = await self._studies.list_instances(series.id)

        overrides: dict[int, Instance] = {
            inst.instance_number: inst
            for inst in phase_instances
            if inst.instance_number is not None
        }
        merged: list[Instance] = []
        for parent_inst in parent_instances:
            number = parent_inst.instance_number
            if number is not None and number in overrides:
                merged.append(overrides[number])
            else:
                merged.append(parent_inst)
        return merged

    async def list_phase_annotations(self, phase: Series) -> list[Annotation]:
        """All annotations attached to instances belonging to this phase."""
        instance_ids = [inst.id for inst in await self._studies.list_instances(phase.id)]
        if not instance_ids:
            return []
        result = await self._db.execute(
            select(Annotation)
            .where(
                Annotation.instance_id.in_(instance_ids),
                Annotation.deleted_at.is_(None),
            )
            .order_by(Annotation.created_at)
        )
        return list(result.scalars().all())

    # ── Write paths ─────────────────────────────────────────────────────

    async def create_phase(
        self,
        parent_series_id: uuid.UUID,
        user_id: uuid.UUID,
        payload: CreatePhaseRequest,
    ) -> Series:
        """Persist a new phase.  Transactional — partial failure rolls back."""
        parent = await self._load_parent_visible(parent_series_id, user_id)
        parent_instances_by_id = await self._load_parent_instances_indexed(parent.id)
        await self._validate_payload_instances(
            payload.instances,
            parent_instances_by_id,
        )

        phase = Series(
            study_id=parent.study_id,
            parent_series_id=parent.id,
            owner_id=user_id,
            series_instance_uid=pydicom.uid.generate_uid(),
            series_number=parent.series_number,
            modality=parent.modality,
            series_description=payload.name,
            body_part_examined=parent.body_part_examined,
            patient_position=parent.patient_position,
            protocol_name=payload.description or parent.protocol_name,
            slice_thickness=parent.slice_thickness,
            spacing_between_slices=parent.spacing_between_slices,
            pixel_spacing=parent.pixel_spacing,
            instance_count=len(payload.instances),
            size_bytes=0,
            storage_path=parent.storage_path,
        )
        self._db.add(phase)
        await self._db.flush()  # populate phase.id

        await self._materialise_instances(phase, payload.instances, parent_instances_by_id)
        await self._db.commit()
        await self._db.refresh(phase)
        return phase

    async def update_phase(
        self,
        phase_id: uuid.UUID,
        user_id: uuid.UUID,
        payload: UpdatePhaseRequest,
    ) -> Series:
        """In-place save.  Rename-only when ``instances`` is omitted.

        When ``instances`` is provided, the phase's existing Instance rows are
        hard-deleted (which cascade-deletes their annotations via the FK
        ``ON DELETE CASCADE``) and the new set is inserted — all in one
        transaction so a half-updated phase can never be observed.
        """
        phase = await self.get_phase(phase_id, user_id)

        if payload.name is not None:
            phase.series_description = payload.name
        if payload.description is not None:
            # We store the optional description in protocol_name to avoid
            # adding a new column for v1 — same rationale as reusing
            # series_description for the phase name.
            phase.protocol_name = payload.description

        if payload.instances is not None:
            parent_id = phase.parent_series_id
            assert parent_id is not None  # phase invariant
            parent_instances_by_id = await self._load_parent_instances_indexed(parent_id)
            await self._validate_payload_instances(
                payload.instances,
                parent_instances_by_id,
            )

            # Hard-delete every Instance row of this phase.  Annotations go
            # with them via FK CASCADE — no separate query needed.
            await self._db.execute(delete(Instance).where(Instance.series_id == phase.id))
            await self._db.flush()

            await self._materialise_instances(phase, payload.instances, parent_instances_by_id)
            phase.instance_count = len(payload.instances)

        await self._db.commit()
        await self._db.refresh(phase)
        return phase

    async def delete_phase(self, phase_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Hard-delete a phase.  Derived MinIO blobs are NOT touched — they
        are content-addressed and may belong to other phases."""
        phase = await self.get_phase(phase_id, user_id)
        await self._db.delete(phase)
        await self._db.commit()

    # ── Internals ───────────────────────────────────────────────────────

    async def _load_phase(self, phase_id: uuid.UUID) -> Series:
        result = await self._db.execute(
            select(Series).where(
                Series.id == phase_id,
                Series.parent_series_id.is_not(None),
            )
        )
        phase = result.scalar_one_or_none()
        if phase is None:
            raise NotFoundError("Phase not found.")
        return phase

    async def _load_parent_visible(
        self,
        parent_series_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Series:
        """Resolve the parent series, enforcing study-level visibility."""
        result = await self._db.execute(
            select(Series).where(
                Series.id == parent_series_id,
                Series.parent_series_id.is_(None),  # phases can't be parents
            )
        )
        parent = result.scalar_one_or_none()
        if parent is None:
            raise NotFoundError("Series not found.")
        # Visibility on the parent study (owner or share).
        await self._studies.get_visible_study(parent.study_id, user_id)
        return parent

    async def _load_parent_instances_indexed(
        self, parent_series_id: uuid.UUID
    ) -> dict[uuid.UUID, Instance]:
        instances = await self._studies.list_instances(parent_series_id)
        return {inst.id: inst for inst in instances}

    async def _validate_payload_instances(
        self,
        items: Iterable[PhaseInstancePayload],
        parent_instances_by_id: dict[uuid.UUID, Instance],
    ) -> None:
        """Cross-series guard + blob-existence guard.

        Rejects 422 on:
        * a ``parent_instance_id`` that doesn't belong to the parent series
        * a ``derived_object_key`` that doesn't exist in MinIO
        """
        bucket = self._settings.minio_bucket_dicom
        seen: set[uuid.UUID] = set()
        for item in items:
            if item.parent_instance_id not in parent_instances_by_id:
                raise ValidationError(
                    f"Instance {item.parent_instance_id} does not belong to this series."
                )
            if item.parent_instance_id in seen:
                raise ValidationError(f"Duplicate entry for instance {item.parent_instance_id}.")
            seen.add(item.parent_instance_id)

            if item.derived_object_key is not None and not self._storage.object_exists(
                bucket, item.derived_object_key
            ):
                raise ValidationError(f"Derived object {item.derived_object_key} does not exist.")

    async def _materialise_instances(
        self,
        phase: Series,
        items: list[PhaseInstancePayload],
        parent_instances_by_id: dict[uuid.UUID, Instance],
    ) -> None:
        """Insert phase Instance rows + their annotations.

        Each phase Instance copies its parent's DICOM metadata verbatim (so the
        merged stack is dimensionally consistent) and overrides ``file_path``
        when a derived blob was supplied.  ``sop_instance_uid`` is freshly
        generated — phases never reuse parent SOP UIDs since the rows live
        under the phase's own series.
        """
        for item in items:
            parent_inst = parent_instances_by_id[item.parent_instance_id]
            phase_inst = Instance(
                series_id=phase.id,
                sop_instance_uid=pydicom.uid.generate_uid(),
                sop_class_uid=parent_inst.sop_class_uid,
                instance_number=parent_inst.instance_number,
                rows=parent_inst.rows,
                columns=parent_inst.columns,
                bits_allocated=parent_inst.bits_allocated,
                bits_stored=parent_inst.bits_stored,
                pixel_representation=parent_inst.pixel_representation,
                number_of_frames=parent_inst.number_of_frames,
                window_center=parent_inst.window_center,
                window_width=parent_inst.window_width,
                rescale_intercept=parent_inst.rescale_intercept,
                rescale_slope=parent_inst.rescale_slope,
                image_position_patient=parent_inst.image_position_patient,
                image_orientation_patient=parent_inst.image_orientation_patient,
                transfer_syntax_uid=parent_inst.transfer_syntax_uid,
                file_path=item.derived_object_key or parent_inst.file_path,
                file_size_bytes=parent_inst.file_size_bytes,
                content_type=parent_inst.content_type,
                checksum_sha256=parent_inst.checksum_sha256,
            )
            self._db.add(phase_inst)
            await self._db.flush()  # populate phase_inst.id for annotations

            for ann_payload in item.annotations:
                self._db.add(self._build_annotation(phase_inst.id, phase.owner_id, ann_payload))

    @staticmethod
    def _build_annotation(
        instance_id: uuid.UUID,
        owner_id: uuid.UUID | None,
        payload: AnnotationPayload,
    ) -> Annotation:
        # owner_id can only be None at the DB level if the phase's creator was
        # deleted (ON DELETE SET NULL).  That can't happen on a fresh save —
        # narrow the type here so we never persist a NULL user_id.
        if owner_id is None:
            raise PermissionDeniedError("Phase owner is required to save annotations.")
        return Annotation(
            instance_id=instance_id,
            user_id=owner_id,
            cornerstone_uid=payload.cornerstone_uid,
            tool_type=payload.tool_type,
            annotation_data=payload.annotation_data,
            viewport_state=payload.viewport_state,
            measurement_value=payload.measurement_value,
            measurement_unit=payload.measurement_unit,
            measurement_area=payload.measurement_area,
            measurement_mean=payload.measurement_mean,
            measurement_stddev=payload.measurement_stddev,
            label=payload.label,
            color=payload.color,
            is_visible=payload.is_visible,
            is_locked=payload.is_locked,
        )
