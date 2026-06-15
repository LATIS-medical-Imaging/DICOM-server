# Deploy to a VPS (single-port Nginx setup)

This guide deploys the full DICOM platform (API + worker + storage + frontend)
on a single VPS using Docker Compose and Nginx as a reverse proxy. Everything
runs behind a single port.

```
Browser ──► :555 Nginx
               ├── /              → Angular frontend (static files)
               ├── /api/          → FastAPI backend  (127.0.0.1:8000)
               ├── /api/v1/ws/    → WebSocket upgrade
               └── /storage/      → MinIO S3 API     (minio:9000 via Docker)
```

---

## Prerequisites

| Requirement        | Minimum              |
|--------------------|----------------------|
| OS                 | Ubuntu 22.04 / Debian 12 / any Linux with Docker |
| RAM                | 4 GB (8 GB recommended — torch + pydicom are heavy) |
| Disk               | 20 GB + storage for DICOM files |
| Docker             | 24+ with Compose v2  |
| Open ports         | **555** (single port) |

Install Docker if not present:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# log out and back in
```

Install Nginx:

```bash
sudo apt update && sudo apt install -y nginx
```

---

## 1. Clone both repositories

```bash
cd /opt
sudo git clone <your-backend-repo-url> dicom-server
sudo git clone <your-frontend-repo-url> dicom-viewer
sudo chown -R $USER:$USER /opt/dicom-server /opt/dicom-viewer
```

---

## 2. Configure the backend

```bash
cd /opt/dicom-server
cp .env.prod.example .env
```

Edit `.env` — fill in at minimum:

```env
# Application
APP_ENV=production
APP_DEBUG=false
APP_LOG_LEVEL=INFO

# IMPORTANT: replace YOUR_VPS_IP everywhere below
CORS_ORIGINS=http://YOUR_VPS_IP:555
CORS_MINIO_ORIGIN=http://YOUR_VPS_IP:555

# Security — generate a real secret
# python3 -c "import secrets; print(secrets.token_urlsafe(64))"
JWT_SECRET_KEY=<generated-secret>

# Database (internal Docker network — no changes needed)
POSTGRES_USER=dicom
POSTGRES_PASSWORD=<strong-password>
POSTGRES_DB=dicom
POSTGRES_HOST=pgbouncer
POSTGRES_PORT=6432

# Redis
REDIS_HOST=redis
REDIS_PORT=6379

# MinIO — Nginx proxies bucket paths to the internal MinIO container
MINIO_ENDPOINT=minio:9000
MINIO_EXTERNAL_ENDPOINT=http://YOUR_VPS_IP:555
MINIO_ACCESS_KEY=<strong-key>
MINIO_SECRET_KEY=<strong-secret>
MINIO_SECURE=false
MINIO_BUCKET_DICOM=dicom-files
MINIO_BUCKET_THUMBNAILS=thumbnails

# Admin bootstrap
ADMIN_BOOTSTRAP_EMAIL=admin@example.com
ADMIN_BOOTSTRAP_PASSWORD=<min-12-chars>
ADMIN_BOOTSTRAP_FIRST_NAME=Admin
ADMIN_BOOTSTRAP_LAST_NAME=User
```

Key points:
- `MINIO_EXTERNAL_ENDPOINT` is `http://YOUR_VPS_IP:555` (no path suffix) — the
  MinIO SDK generates presigned URLs at the root (`/dicom-files/...`), and Nginx
  matches the bucket name prefixes to proxy them to MinIO.
- `CORS_ORIGINS` and `CORS_MINIO_ORIGIN` must include the port
  (`http://YOUR_VPS_IP:555`).

---

## 3. Build and start the backend

```bash
cd /opt/dicom-server
docker compose -f docker-compose.prod.yml up -d --build
```

Wait for healthy status:

```bash
docker compose -f docker-compose.prod.yml ps
# All services should show "Up" / "healthy"
```

Run migrations and seed admin:

```bash
docker compose -f docker-compose.prod.yml exec api alembic upgrade head
docker compose -f docker-compose.prod.yml exec api python -m app.cli.seed_admin
```

Verify the API is listening internally:

```bash
curl http://127.0.0.1:8000/api/v1/health/ready
# {"status":"ok","checks":[...]}
```

---

## 4. Build the frontend

```bash
cd /opt/dicom-viewer
```

Set the production API URL. Edit `src/environments/environment.prod.ts`:

```typescript
export const environment = {
  production: true,
  apiBaseUrl: 'http://YOUR_VPS_IP:555/api/v1',
} as const;
```

Raise the Angular CSS budget limits (the Cornerstone.js viewer components exceed
the defaults):

```bash
sed -i 's/"maximumWarning": "4kB"/"maximumWarning": "12kB"/g' angular.json
sed -i 's/"maximumError": "8kB"/"maximumError": "16kB"/g' angular.json
```

Build:

```bash
# Option A: build inside Docker (no local Node needed)
docker run --rm -v "$PWD":/app -w /app node:20-alpine sh -c \
  "npm ci && npx ng build --configuration=production"

# Option B: if Node 20 is installed locally
npm ci
npx ng build --configuration=production
```

The output lands in `dist/dicom-viewer/browser/` (Angular 19 with application builder).
Verify:

```bash
ls /opt/dicom-viewer/dist/dicom-viewer/browser/index.html
```

---

## 5. Configure Nginx

Create `/etc/nginx/sites-available/dicom`:

```nginx
server {
    listen 555;
    server_name _;

    # ---------- Frontend (Angular static files) ----------
    root /opt/dicom-viewer/dist/dicom-viewer/browser;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    # ---------- API reverse proxy ----------
    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # File uploads can be large (DICOM files)
        client_max_body_size 500M;
    }

    # ---------- WebSocket (chat) ----------
    location /api/v1/ws/ {
        proxy_pass http://127.0.0.1:8000/api/v1/ws/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host       $host;
        proxy_set_header X-Real-IP  $remote_addr;
        proxy_read_timeout 86400s;
    }

    # ---------- MinIO (presigned URL proxy) ----------
    # The MinIO SDK generates presigned URLs at the root path using the
    # bucket name as the first segment (e.g. /dicom-files/..., /thumbnails/...).
    # This regex matches both bucket prefixes and proxies to MinIO.
    location ~ ^/(dicom-files|thumbnails)/ {
        proxy_pass http://127.0.0.1:9000;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # DICOM uploads can be large
        client_max_body_size 500M;
    }
}
```

MinIO is exposed on `127.0.0.1:9000` only (set in `docker-compose.prod.yml`)
so Nginx can reach it but it's not accessible from the internet.

Enable Nginx and start:

```bash
sudo ln -sf /etc/nginx/sites-available/dicom /etc/nginx/sites-enabled/dicom
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t            # must say "syntax is ok"
sudo systemctl reload nginx
```

---

## 6. Verify the full stack

From your local machine (replace `YOUR_VPS_IP`):

```bash
# Health check
curl http://YOUR_VPS_IP:555/api/v1/health/ready

# Frontend
curl -s http://YOUR_VPS_IP:555/ | head -5
# Should show Angular's index.html

# MinIO (should return AccessDenied XML — that's correct, means the proxy works)
curl -s http://YOUR_VPS_IP:555/storage/
```

Open `http://YOUR_VPS_IP:555` in a browser — you should see the login page.
Log in with the admin credentials from `.env`.

---

## 7. Firewall

Only ports 22 (SSH) and 555 (HTTP) need to be open:

```bash
sudo ufw allow 22/tcp
sudo ufw allow 555/tcp
sudo ufw enable
```

All other services (Postgres, Redis, PgBouncer, MinIO) are either internal-only
or bound to `127.0.0.1`.

---

## Port mapping summary

| Service        | Container port | Host binding         | Visibility |
|----------------|---------------|----------------------|------------|
| Nginx          | —             | **0.0.0.0:555**      | Public     |
| FastAPI API    | 8000          | 127.0.0.1:8000       | Localhost  |
| MinIO S3 API   | 9000          | 127.0.0.1:9000       | Localhost  |
| Postgres       | 5432          | —                    | Internal   |
| PgBouncer      | 6432          | —                    | Internal   |
| Redis          | 6379          | —                    | Internal   |
| MinIO Console  | 9001          | —                    | Internal   |

---

## Quick reference

| Action                  | Command                                                                        |
|-------------------------|--------------------------------------------------------------------------------|
| View logs               | `cd /opt/dicom-server && docker compose -f docker-compose.prod.yml logs -f api worker` |
| Restart backend         | `cd /opt/dicom-server && docker compose -f docker-compose.prod.yml restart api worker` |
| Run new migration       | `docker compose -f docker-compose.prod.yml exec api alembic upgrade head`      |
| Rebuild after code pull | `docker compose -f docker-compose.prod.yml up -d --build`                      |
| Rebuild frontend        | See step 4, then `sudo systemctl reload nginx`                                 |
| Stop everything         | `docker compose -f docker-compose.prod.yml down` (add `-v` to wipe volumes)    |

---

## Accessing internal services

Use SSH tunnels or `docker exec`:

```bash
# Postgres shell
docker compose -f docker-compose.prod.yml exec postgres psql -U dicom -d dicom

# MinIO console (web UI) — SSH tunnel then open http://localhost:9001
ssh -L 9001:<MINIO_CONTAINER_IP>:9001 user@YOUR_VPS_IP
```

---

## Adding HTTPS later (with a domain)

Since port 443 is not available, use **Cloudflare Tunnel**:

1. Install `cloudflared` on the VPS
2. Create a tunnel mapping your domain to `localhost:555`
3. HTTPS is handled by Cloudflare automatically — no cert management

Then update:
1. `.env` → `CORS_ORIGINS=https://your-domain.com`
2. `.env` → `MINIO_EXTERNAL_ENDPOINT=https://your-domain.com/storage`
3. `.env` → `CORS_MINIO_ORIGIN=https://your-domain.com`
4. `environment.prod.ts` → `apiBaseUrl: 'https://your-domain.com/api/v1'`
5. Rebuild frontend and restart backend.