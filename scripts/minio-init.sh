#!/bin/sh
# One-shot MinIO initialisation script.
# Runs inside the minio/mc container after MinIO is healthy.
# - Configures the mc alias
# - Creates application buckets
# - Sets CORS policy on each bucket so the browser can PUT/GET directly

set -e

ALIAS="local"
ENDPOINT="http://minio:9000"
ORIGIN="${CORS_MINIO_ORIGIN:-http://localhost:4200}"

echo "==> Connecting to MinIO at $ENDPOINT ..."
until mc alias set "$ALIAS" "$ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" > /dev/null 2>&1; do
  echo "    MinIO not ready yet — retrying in 2 s"
  sleep 2
done
echo "    Connected."

# Buckets 
for BUCKET in dicom-files thumbnails voice-messages; do
  if mc ls "$ALIAS/$BUCKET" > /dev/null 2>&1; then
    echo "==> Bucket '$BUCKET' already exists — skipping."
  else
    mc mb "$ALIAS/$BUCKET"
    echo "==> Bucket '$BUCKET' created."
  fi
done

# CORS is configured via MINIO_API_CORS_ALLOW_ORIGIN env var on the MinIO
# server itself (in docker-compose.yml). Single-node MinIO does not support
# the S3 PutBucketCors API, so mc cors set will not work here.

echo "==> MinIO initialisation complete."
