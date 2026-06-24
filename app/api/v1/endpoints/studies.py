"""Study, series, and instance metadata endpoints.

All endpoints require a valid access token. The current user is derived from
that token — never from a request parameter — and the ``StudyService``
visibility helpers enforce owner-or-share access control.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import CacheDep, CurrentUser, DBSession, SettingsDep, StorageDep
from app.core.exceptions import NotFoundError
from app.db.models.study import Study
from app.db.models.user import User
from app.schemas.chat import UserSearchResult
from app.schemas.shares import ShareSourceDto
from app.schemas.studies import (
    InstanceResponse,
    SeriesResponse,
    StudyListResponse,
    StudyResponse,
    ViewerInstanceResponse,
    ViewerSeriesResponse,
    ViewerStudyResponse,
)
from app.services.phase_service import PhaseService
from app.services.share_service import ShareService
from app.services.study_service import StudyService
from app.services.ws_hub import get_ws_hub

router = APIRouter()


async def _serialise_study(
    db: DBSession,
    study: Study,
    user_id: uuid.UUID,
) -> StudyResponse:
    """Serialise a Study, populating ``share_source`` for non-owners.

    For owned studies returns the bare DTO; for studies the caller can only
    see through a share, attaches the active Share row's id + grantor +
    permission so the frontend's sidebar can render the "Shared by Dr X ·
    Annotate" subtitle and the viewer can gate write actions.
    """
    response = StudyResponse.model_validate(study)
    if study.owner_id != user_id:
        share_service = ShareService(db, get_ws_hub())
        share = await share_service.active_share_row_for_study(user_id, study.id)
        if share is not None:
            grantor = await db.get(User, share.grantor_id)
            if grantor is not None:
                response.share_source = ShareSourceDto(
                    share_id=share.id,
                    grantor=UserSearchResult.model_validate(grantor),
                    permission=share.permission,
                )
    return response


@router.get(
    "",
    response_model=StudyListResponse,
    summary="List studies visible to the authenticated user (owned + shared)",
)
async def list_studies(db: DBSession, user: CurrentUser, cache: CacheDep) -> StudyListResponse:
    # Cache hit — deserialise directly without touching the DB.
    if cached := await cache.get_study_list(user.id):
        items = [StudyResponse.model_validate(d) for d in cached]
        return StudyListResponse(items=items, total=len(items))

    studies, total = await StudyService(db).list_visible(user.id)
    items = [await _serialise_study(db, s, user.id) for s in studies]

    await cache.set_study_list(user.id, [i.model_dump(mode="json") for i in items])
    return StudyListResponse(items=items, total=total)


@router.get(
    "/{study_id}",
    response_model=StudyResponse,
    summary="Get a single study (must be owned by or shared with the caller)",
)
async def get_study(study_id: uuid.UUID, db: DBSession, user: CurrentUser) -> StudyResponse:
    study = await StudyService(db).get_visible_study(study_id, user.id)
    return await _serialise_study(db, study, user.id)


@router.get(
    "/{study_id}/series",
    response_model=list[SeriesResponse],
    summary="List all series in a study",
)
async def list_series(
    study_id: uuid.UUID,
    db: DBSession,
    user: CurrentUser,
    cache: CacheDep,
) -> list[SeriesResponse]:
    service = StudyService(db)
    study = await service.get_visible_study(study_id, user.id)  # 404 if not visible

    # Series cache is only safe for study owners — non-owners may receive
    # a share-filtered subset, so their requests fall through to the DB.
    is_owner = study.owner_id == user.id
    if is_owner:
        if cached := await cache.get_series_list(study_id):
            return [SeriesResponse.model_validate(d) for d in cached]

    series = await service.list_series(study_id, viewer_id=user.id)
    result = [SeriesResponse.model_validate(s) for s in series]

    if is_owner:
        await cache.set_series_list(study_id, [r.model_dump(mode="json") for r in result])
    return result


@router.get(
    "/{study_id}/series/{series_id}/instances",
    response_model=list[InstanceResponse],
    summary="List all instances in a series (phase-aware merged stack)",
)
async def list_instances(
    study_id: uuid.UUID,
    series_id: uuid.UUID,
    db: DBSession,
    user: CurrentUser,
    storage: StorageDep,
    settings: SettingsDep,
    cache: CacheDep,
) -> list[InstanceResponse]:
    """Return the ordered instance stack.

    For an original series this is identical to its row-set ordered by
    ``instance_number``.  For a phase, the parent's instances are merged with
    the phase's overrides spliced in at the matching ``instance_number`` —
    the viewer doesn't need to know which kind it loaded.
    """
    service = StudyService(db)
    await service.get_visible_study(study_id, user.id)

    series = await service.get_series(series_id)
    if series is None or series.study_id != study_id:
        raise NotFoundError("Series not found.")

    # Phases enforce their own private-to-owner visibility on top of the
    # parent-study visibility we already checked.
    if series.parent_series_id is not None and series.owner_id != user.id:
        raise NotFoundError("Series not found.")

    is_original = series.parent_series_id is None
    if is_original:
        if cached := await cache.get_instances(series_id):
            return [InstanceResponse.model_validate(d) for d in cached]

    phases = PhaseService(db, storage, settings)
    instances = await phases.list_instances_rendered(series)
    result = [InstanceResponse.model_validate(i) for i in instances]

    if is_original:
        await cache.set_instances(series_id, [r.model_dump(mode="json") for r in result])
    return result


@router.get(
    "/{study_id}/viewer",
    response_model=ViewerStudyResponse,
    summary="Get study + all series + all instances + presigned download URLs in one call",
)
async def get_study_for_viewer(
    study_id: uuid.UUID,
    db: DBSession,
    user: CurrentUser,
    storage: StorageDep,
    settings: SettingsDep,
    cache: CacheDep,
) -> ViewerStudyResponse:
    """Aggregated viewer payload — replaces the N+1 call chain.

    Performs a single authenticated request and returns the full hierarchy:
    study metadata → series list → per-series instances → presigned GET URLs
    for every instance's pixel data.  The client can start rendering
    immediately without further API calls.
    """
    service = StudyService(db)
    study = await service.get_visible_study(study_id, user.id)
    study_dto = await _serialise_study(db, study, user.id)

    all_series = await service.list_series(study_id, viewer_id=user.id)
    bucket = settings.minio_bucket_dicom
    expires = settings.minio_presigned_url_expire_seconds
    phases = PhaseService(db, storage, settings)

    viewer_series: list[ViewerSeriesResponse] = []
    for series in all_series:
        instances = await phases.list_instances_rendered(series)
        viewer_instances: list[ViewerInstanceResponse] = []
        for inst in instances:
            # Check presign cache to avoid a MinIO HMAC-sign call per instance.
            url = await cache.get_presign(inst.file_path)
            if url is None:
                url = storage.presigned_get_url(bucket, inst.file_path, expires_seconds=expires)
                await cache.set_presign(inst.file_path, url, expires)
            viewer_instances.append(
                ViewerInstanceResponse(
                    **InstanceResponse.model_validate(inst).model_dump(),
                    download_url=url,
                    expires_in=expires,
                )
            )
        viewer_series.append(
            ViewerSeriesResponse(
                **SeriesResponse.model_validate(series).model_dump(),
                instances=viewer_instances,
            )
        )

    return ViewerStudyResponse(
        **study_dto.model_dump(),
        series=viewer_series,
    )


@router.delete(
    "/{study_id}/series/{series_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an original series (study owner) or a phase (creator only)",
)
async def delete_series(
    study_id: uuid.UUID,
    series_id: uuid.UUID,
    db: DBSession,
    user: CurrentUser,
    cache: CacheDep,
) -> None:
    service = StudyService(db)
    study = await service.get_visible_study(study_id, user.id)

    series = await service.get_series(series_id)
    if series is None or series.study_id != study_id:
        raise NotFoundError("Series not found.")

    if series.parent_series_id is None:
        # Original series — owner_id is NULL; ownership belongs to the study.
        # Only the study owner may delete an original (and its cascaded phases).
        if study.owner_id != user.id:
            raise NotFoundError("Series not found.")
    else:
        # Phase — private to creator; owner_id is the doctor who saved it.
        if series.owner_id != user.id:
            raise NotFoundError("Series not found.")

    await service.delete_series(series)

    # Invalidate after successful deletion.  The study list changes because
    # total_series_count / total_instance_count on the study row may be
    # updated; series and instance lists are now stale.
    await cache.invalidate_series_list(study_id)
    await cache.invalidate_instances(series_id)
    await cache.invalidate_study_list(study.owner_id)
