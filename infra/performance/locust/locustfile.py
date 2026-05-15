"""Locust performance tests for DICOM server.

Realistic medical imaging workloads targeting actual API endpoints.
"""

from __future__ import annotations

import json
import os
import random
import uuid
from pathlib import Path

from locust import HttpUser, between, events, tag, task
from locust.runners import MasterRunner


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATASET_PATH = Path(os.getenv("DATASET_PATH", "/perf/datasets"))
OWNER_ID = os.getenv("BENCHMARK_OWNER_ID", "00000000-0000-0000-0000-000000000001")

FILTERS = ["top_hat", "kmeans", "fcm", "pfcm", "febds", "breast_mask"]
FILTER_PARAMS: dict[str, dict] = {
    "top_hat": {"radius": 4},
    "kmeans": {"k": 2},
    "fcm": {"c": 2},
    "pfcm": {"c": 2},
    "febds": {"method": "dog"},
    "breast_mask": {"mask_only": False},
}

# ---------------------------------------------------------------------------
# Dataset discovery
# ---------------------------------------------------------------------------

_dicom_files: list[Path] = []


def _discover_dicoms() -> list[Path]:
    """Recursively find .dcm files in the dataset directory."""
    if not DATASET_PATH.exists():
        return []
    return sorted(DATASET_PATH.rglob("*.dcm"))


@events.init.add_listener
def on_init(environment, **kwargs):  # type: ignore[no-untyped-def]
    global _dicom_files
    if not isinstance(environment.runner, MasterRunner):
        _dicom_files = _discover_dicoms()
        count = len(_dicom_files)
        print(f"[perf] Discovered {count} DICOM files in {DATASET_PATH}")


# ---------------------------------------------------------------------------
# Shared state for created resources
# ---------------------------------------------------------------------------

_created_studies: list[dict] = []
_created_instances: list[dict] = []


# ---------------------------------------------------------------------------
# User definitions
# ---------------------------------------------------------------------------


class DicomUploadUser(HttpUser):
    """Simulates DICOM upload workflows."""

    wait_time = between(1, 3)
    weight = 3

    def _random_uids(self) -> tuple[str, str, str]:
        study_uid = f"1.2.840.{random.randint(100000, 999999)}"
        series_uid = f"{study_uid}.1"
        sop_uid = f"{series_uid}.{random.randint(1, 9999)}"
        return study_uid, series_uid, sop_uid

    @tag("upload", "presign")
    @task(5)
    def presign_upload(self) -> None:
        """Request a presigned upload URL."""
        study_uid, series_uid, sop_uid = self._random_uids()
        payload = {
            "owner_id": OWNER_ID,
            "study_instance_uid": study_uid,
            "series_instance_uid": series_uid,
            "sop_instance_uid": sop_uid,
            "file_size_bytes": random.randint(5_000_000, 50_000_000),
        }
        with self.client.post(
            "/api/v1/presign/upload",
            json=payload,
            name="/api/v1/presign/upload",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Status {resp.status_code}")

    @tag("upload", "full-flow")
    @task(2)
    def full_upload_flow(self) -> None:
        """Full upload: presign -> PUT to MinIO -> create job -> poll."""
        if not _dicom_files:
            return

        dcm_path = random.choice(_dicom_files)
        dcm_bytes = dcm_path.read_bytes()
        study_uid, series_uid, sop_uid = self._random_uids()

        # Step 1: Get presigned URL
        presign_payload = {
            "owner_id": OWNER_ID,
            "study_instance_uid": study_uid,
            "series_instance_uid": series_uid,
            "sop_instance_uid": sop_uid,
            "file_size_bytes": len(dcm_bytes),
        }
        with self.client.post(
            "/api/v1/presign/upload",
            json=presign_payload,
            name="/api/v1/presign/upload [flow]",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"Presign failed: {resp.status_code}")
                return
            resp.success()
            presign_data = resp.json()

        object_key = presign_data["object_key"]
        upload_url = presign_data["upload_url"]

        # Step 2: Upload to MinIO (direct PUT)
        import urllib.request

        try:
            req = urllib.request.Request(
                upload_url,
                data=dcm_bytes,
                method="PUT",
                headers={"Content-Type": "application/dicom"},
            )
            urllib.request.urlopen(req, timeout=60)
        except Exception:
            return

        # Step 3: Create upload job
        job_payload = {
            "owner_id": OWNER_ID,
            "object_key": object_key,
            "file_size_bytes": len(dcm_bytes),
        }
        with self.client.post(
            "/api/v1/uploads",
            json=job_payload,
            name="/api/v1/uploads [create]",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201, 202):
                resp.success()
                job_data = resp.json()
                job_id = job_data.get("id")

                # Step 4: Poll job status
                if job_id:
                    for _ in range(5):
                        with self.client.get(
                            f"/api/v1/uploads/{job_id}",
                            name="/api/v1/uploads/{job_id} [poll]",
                            catch_response=True,
                        ) as poll_resp:
                            if poll_resp.status_code == 200:
                                poll_resp.success()
                                status = poll_resp.json().get("status")
                                if status in ("completed", "failed"):
                                    break
                            else:
                                poll_resp.failure(f"Poll failed: {poll_resp.status_code}")
                                break
            else:
                resp.failure(f"Upload job failed: {resp.status_code}")


class MetadataUser(HttpUser):
    """Simulates metadata retrieval workflows."""

    wait_time = between(0.5, 2)
    weight = 5

    @tag("metadata", "health")
    @task(2)
    def health_check(self) -> None:
        """Hit the readiness endpoint."""
        with self.client.get(
            "/api/v1/health/ready",
            name="/api/v1/health/ready",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Status {resp.status_code}")

    @tag("metadata", "studies")
    @task(5)
    def list_studies(self) -> None:
        """List studies for owner."""
        with self.client.get(
            f"/api/v1/studies?owner_id={OWNER_ID}",
            name="/api/v1/studies",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
                data = resp.json()
                items = data.get("items", [])
                if items:
                    _created_studies.clear()
                    _created_studies.extend(items[:20])
            else:
                resp.failure(f"Status {resp.status_code}")

    @tag("metadata", "study-detail")
    @task(3)
    def get_study_detail(self) -> None:
        """Get single study and its series."""
        if not _created_studies:
            self.list_studies()
            return

        study = random.choice(_created_studies)
        study_id = study.get("id")
        if not study_id:
            return

        with self.client.get(
            f"/api/v1/studies/{study_id}",
            name="/api/v1/studies/{study_id}",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Status {resp.status_code}")

    @tag("metadata", "series")
    @task(3)
    def list_series(self) -> None:
        """List series for a study."""
        if not _created_studies:
            return

        study = random.choice(_created_studies)
        study_id = study.get("id")
        if not study_id:
            return

        with self.client.get(
            f"/api/v1/studies/{study_id}/series",
            name="/api/v1/studies/{study_id}/series",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
                series_list = resp.json()
                if series_list:
                    series = random.choice(series_list)
                    series_id = series.get("id")
                    if series_id:
                        self._list_instances(study_id, series_id)
            else:
                resp.failure(f"Status {resp.status_code}")

    def _list_instances(self, study_id: str, series_id: str) -> None:
        """List instances for a series."""
        with self.client.get(
            f"/api/v1/studies/{study_id}/series/{series_id}/instances",
            name="/api/v1/studies/{study_id}/series/{series_id}/instances",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
                instances = resp.json()
                if instances:
                    for inst in instances[:5]:
                        if inst not in _created_instances:
                            _created_instances.append(inst)
            else:
                resp.failure(f"Status {resp.status_code}")


class InferenceUser(HttpUser):
    """Simulates inference/processing workloads."""

    wait_time = between(2, 5)
    weight = 2

    @tag("inference", "processing")
    @task
    def apply_filter(self) -> None:
        """Apply a medical imaging filter to an instance."""
        if not _created_instances:
            return

        instance = random.choice(_created_instances)
        instance_id = instance.get("id")
        if not instance_id:
            return

        filter_name = random.choice(FILTERS)
        params = FILTER_PARAMS.get(filter_name, {})

        payload = {
            "instance_id": instance_id,
            "filter": filter_name,
            "params": params,
        }
        with self.client.post(
            "/api/v1/processing/apply",
            json=payload,
            name=f"/api/v1/processing/apply [{filter_name}]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Filter {filter_name} failed: {resp.status_code}")

    @tag("inference", "download")
    @task
    def download_presigned(self) -> None:
        """Get presigned download URL for an instance."""
        if not _created_instances:
            return

        instance = random.choice(_created_instances)
        file_path = instance.get("file_path")
        if not file_path:
            return

        with self.client.get(
            f"/api/v1/presign/download?object_key={file_path}",
            name="/api/v1/presign/download",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Download presign failed: {resp.status_code}")


class MixedWorkloadUser(HttpUser):
    """Combines upload, metadata, and inference in one user."""

    wait_time = between(1, 4)
    weight = 2

    @tag("mixed")
    @task(3)
    def browse_and_process(self) -> None:
        """Simulate a doctor browsing studies and running inference."""
        # List studies
        with self.client.get(
            f"/api/v1/studies?owner_id={OWNER_ID}",
            name="/api/v1/studies [mixed]",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"Status {resp.status_code}")
                return
            resp.success()
            studies = resp.json().get("items", [])

        if not studies:
            return

        study = random.choice(studies)
        study_id = study.get("id")
        if not study_id:
            return

        # Get series
        with self.client.get(
            f"/api/v1/studies/{study_id}/series",
            name="/api/v1/studies/{study_id}/series [mixed]",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"Status {resp.status_code}")
                return
            resp.success()
            series_list = resp.json()

        if not series_list:
            return

        series = random.choice(series_list)
        series_id = series.get("id")
        if not series_id:
            return

        # Get instances
        with self.client.get(
            f"/api/v1/studies/{study_id}/series/{series_id}/instances",
            name="/api/v1/studies/{study_id}/series/{series_id}/instances [mixed]",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"Status {resp.status_code}")
                return
            resp.success()
            instances = resp.json()

        if not instances:
            return

        # Apply filter to random instance
        instance = random.choice(instances)
        instance_id = instance.get("id")
        if not instance_id:
            return

        filter_name = random.choice(FILTERS)
        payload = {
            "instance_id": instance_id,
            "filter": filter_name,
            "params": FILTER_PARAMS.get(filter_name, {}),
        }
        with self.client.post(
            "/api/v1/processing/apply",
            json=payload,
            name=f"/api/v1/processing/apply [{filter_name}] [mixed]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Mixed filter failed: {resp.status_code}")