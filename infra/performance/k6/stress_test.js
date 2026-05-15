/**
 * k6 stress test — progressively increase load until saturation.
 */

import http from "k6/http";
import { check, group, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

const BASE_URL = __ENV.API_BASE_URL || "http://localhost:8000";
const OWNER_ID =
  __ENV.BENCHMARK_OWNER_ID || "00000000-0000-0000-0000-000000000001";

const errorRate = new Rate("error_rate");
const requestCount = new Counter("total_requests");

export const options = {
  scenarios: {
    stress: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "1m", target: 5 },
        { duration: "2m", target: 15 },
        { duration: "2m", target: 30 },
        { duration: "2m", target: 50 },
        { duration: "2m", target: 75 },
        { duration: "1m", target: 0 },
      ],
      gracefulRampDown: "15s",
    },
  },
  thresholds: {
    http_req_duration: ["p(99)<15000"],
    error_rate: ["rate<0.5"],
  },
};

function randomUID() {
  return `1.2.840.${Math.floor(Math.random() * 900000 + 100000)}`;
}

const params = {
  headers: { "Content-Type": "application/json" },
  timeout: "30s",
};

export default function () {
  // Health
  const health = http.get(`${BASE_URL}/api/v1/health/ready`, { timeout: "10s" });
  requestCount.add(1);
  errorRate.add(health.status !== 200);

  // List studies
  const studiesRes = http.get(
    `${BASE_URL}/api/v1/studies?owner_id=${OWNER_ID}`,
    params
  );
  requestCount.add(1);
  errorRate.add(studiesRes.status !== 200);

  // Presign upload
  const uid = randomUID();
  const presignRes = http.post(
    `${BASE_URL}/api/v1/presign/upload`,
    JSON.stringify({
      owner_id: OWNER_ID,
      study_instance_uid: uid,
      series_instance_uid: `${uid}.1`,
      sop_instance_uid: `${uid}.1.1`,
      file_size_bytes: 30000000,
    }),
    params
  );
  requestCount.add(1);
  errorRate.add(presignRes.status !== 200);

  // Drill into studies if available
  if (studiesRes.status === 200) {
    try {
      const items = JSON.parse(studiesRes.body).items || [];
      if (items.length > 0) {
        const study = items[Math.floor(Math.random() * items.length)];
        const seriesRes = http.get(
          `${BASE_URL}/api/v1/studies/${study.id}/series`,
          params
        );
        requestCount.add(1);
        errorRate.add(seriesRes.status !== 200);
      }
    } catch (_) {}
  }

  sleep(0.5);
}