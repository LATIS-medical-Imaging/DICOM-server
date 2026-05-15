/**
 * k6 soak/endurance test — low-moderate load over extended duration.
 *
 * Detects memory leaks, connection pool exhaustion, and degradation.
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Counter, Trend } from "k6/metrics";

const BASE_URL = __ENV.API_BASE_URL || "http://localhost:8000";
const OWNER_ID =
  __ENV.BENCHMARK_OWNER_ID || "00000000-0000-0000-0000-000000000001";

const errorRate = new Rate("error_rate");
const requestCount = new Counter("total_requests");
const latencyTrend = new Trend("request_latency", true);

export const options = {
  scenarios: {
    soak: {
      executor: "constant-vus",
      vus: 3,
      duration: "15m",
    },
  },
  thresholds: {
    http_req_duration: ["p(95)<5000"],
    error_rate: ["rate<0.05"],
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
  let res = http.get(`${BASE_URL}/api/v1/health/ready`, { timeout: "10s" });
  requestCount.add(1);
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  // List studies
  res = http.get(`${BASE_URL}/api/v1/studies?owner_id=${OWNER_ID}`, params);
  requestCount.add(1);
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  if (res.status === 200) {
    try {
      const items = JSON.parse(res.body).items || [];
      if (items.length > 0) {
        const study = items[Math.floor(Math.random() * items.length)];

        // Study detail
        res = http.get(`${BASE_URL}/api/v1/studies/${study.id}`, params);
        requestCount.add(1);
        latencyTrend.add(res.timings.duration);
        errorRate.add(res.status !== 200);

        // Series
        res = http.get(
          `${BASE_URL}/api/v1/studies/${study.id}/series`,
          params
        );
        requestCount.add(1);
        latencyTrend.add(res.timings.duration);
        errorRate.add(res.status !== 200);
      }
    } catch (_) {}
  }

  // Presign
  const uid = randomUID();
  res = http.post(
    `${BASE_URL}/api/v1/presign/upload`,
    JSON.stringify({
      owner_id: OWNER_ID,
      study_instance_uid: uid,
      series_instance_uid: `${uid}.1`,
      sop_instance_uid: `${uid}.1.1`,
      file_size_bytes: 20000000,
    }),
    params
  );
  requestCount.add(1);
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  sleep(2);
}