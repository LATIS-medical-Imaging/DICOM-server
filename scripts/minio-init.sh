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
for BUCKET in dicom-files thumbnails; do
  if mc ls "$ALIAS/$BUCKET" > /dev/null 2>&1; then
    echo "==> Bucket '$BUCKET' already exists — skipping."
  else
    mc mb "$ALIAS/$BUCKET"
    echo "==> Bucket '$BUCKET' created."
  fi
done

# Allows the Angular viewer to PUT (upload) and GET (download) directly.
# CORS_MINIO_ORIGIN should be set per environment (http://localhost:4200 for local dev)
cat > /tmp/cors.json << EOF
{
  "CORSRules": [
    {
      "AllowedOrigins": ["$ORIGIN"],
      "AllowedMethods": ["GET", "PUT", "POST", "DELETE", "HEAD"],
      "AllowedHeaders": ["*"],
      "ExposeHeaders": ["ETag", "Content-Length"],
      "MaxAgeSeconds": 3600
    }
  ]
}
EOF

for BUCKET in dicom-files thumbnails; do
  mc cors set "$ALIAS/$BUCKET" /tmp/cors.json
  echo "==> CORS set on '$BUCKET' for origin '$ORIGIN'."
done

echo "==> MinIO initialisation complete."
