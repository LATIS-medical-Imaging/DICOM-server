/**
 * k6 spike/peak test — sudden traffic bursts to test recovery.
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Counter } from "k6/metrics";

const BASE_URL = __ENV.API_BASE_URL || "http://localhost:8000";
const OWNER_ID =
  __ENV.BENCHMARK_OWNER_ID || "00000000-0000-0000-0000-000000000001";

const errorRate = new Rate("error_rate");
const requestCount = new Counter("total_requests");

export const options = {
  scenarios: {
    spike: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "30s", target: 2 },   // warm up
        { duration: "10s", target: 50 },   // spike!
        { duration: "1m", target: 50 },    // hold spike
        { duration: "10s", target: 2 },    // drop
        { duration: "1m", target: 2 },     // recovery
        { duration: "10s", target: 50 },   // second spike
        { duration: "1m", target: 50 },    // hold
        { duration: "30s", target: 0 },    // cooldown
      ],
      gracefulRampDown: "10s",
    },
  },
  thresholds: {
    http_req_duration: ["p(95)<10000"],
    error_rate: ["rate<0.4"],
  },
};

function randomUID() {
  return `1.2.840.${Math.floor(Math.random() * 900000 + 100000)}`;
}

const params = {
  headers: { "Content-Type": "application/json" },
  timeout: "15s",
};

export default function () {
  // Rapid fire: health + studies + presign
  const health = http.get(`${BASE_URL}/api/v1/health/ready`, { timeout: "10s" });
  requestCount.add(1);
  errorRate.add(health.status !== 200);

  const studies = http.get(
    `${BASE_URL}/api/v1/studies?owner_id=${OWNER_ID}`,
    params
  );
  requestCount.add(1);
  errorRate.add(studies.status !== 200);

  const uid = randomUID();
  const presign = http.post(
    `${BASE_URL}/api/v1/presign/upload`,
    JSON.stringify({
      owner_id: OWNER_ID,
      study_instance_uid: uid,
      series_instance_uid: `${uid}.1`,
      sop_instance_uid: `${uid}.1.1`,
      file_size_bytes: 25000000,
    }),
    params
  );
  requestCount.add(1);
  errorRate.add(presign.status !== 200);

  sleep(0.3);
}