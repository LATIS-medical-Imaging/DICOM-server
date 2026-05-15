/**
 * k6 load test for DICOM server.
 *
 * Measures throughput and latency across metadata, presign, and upload endpoints.
 */

import http from "k6/http";
import { check, group, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";
import { SharedArray } from "k6/data";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const BASE_URL = __ENV.API_BASE_URL || "http://localhost:8000";
const OWNER_ID =
  __ENV.BENCHMARK_OWNER_ID || "00000000-0000-0000-0000-000000000001";

// Custom metrics
const uploadPresignLatency = new Trend("upload_presign_latency", true);
const studyListLatency = new Trend("study_list_latency", true);
const seriesListLatency = new Trend("series_list_latency", true);
const instanceListLatency = new Trend("instance_list_latency", true);
const processingLatency = new Trend("processing_latency", true);
const errorRate = new Rate("error_rate");
const requestCount = new Counter("total_requests");

// ---------------------------------------------------------------------------
// Test scenarios
// ---------------------------------------------------------------------------

export const options = {
  scenarios: {
    // Normal load: ramp up to target, hold, ramp down
    load_test: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "30s", target: 5 },
        { duration: "2m", target: 5 },
        { duration: "30s", target: 0 },
      ],
      gracefulRampDown: "10s",
    },
  },
  thresholds: {
    http_req_duration: ["p(95)<5000", "p(99)<10000"],
    error_rate: ["rate<0.1"],
  },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function randomUID() {
  return `1.2.840.${Math.floor(Math.random() * 900000 + 100000)}`;
}

function randomUUID() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

const params = {
  headers: { "Content-Type": "application/json" },
  timeout: "60s",
};

// ---------------------------------------------------------------------------
// Main VU function
// ---------------------------------------------------------------------------

export default function () {
  // Health check
  group("health", () => {
    const res = http.get(`${BASE_URL}/api/v1/health/ready`, { timeout: "10s" });
    requestCount.add(1);
    check(res, { "health 200": (r) => r.status === 200 });
    errorRate.add(res.status !== 200);
  });

  // Metadata: list studies
  let studies = [];
  group("list_studies", () => {
    const res = http.get(
      `${BASE_URL}/api/v1/studies?owner_id=${OWNER_ID}`,
      params
    );
    requestCount.add(1);
    studyListLatency.add(res.timings.duration);
    const ok = check(res, { "studies 200": (r) => r.status === 200 });
    errorRate.add(!ok);
    if (ok) {
      try {
        studies = JSON.parse(res.body).items || [];
      } catch (_) {}
    }
  });

  // Metadata: study detail + series + instances
  if (studies.length > 0) {
    const study = studies[Math.floor(Math.random() * studies.length)];

    group("study_detail", () => {
      const res = http.get(
        `${BASE_URL}/api/v1/studies/${study.id}`,
        params
      );
      requestCount.add(1);
      errorRate.add(res.status !== 200);
    });

    group("list_series", () => {
      const res = http.get(
        `${BASE_URL}/api/v1/studies/${study.id}/series`,
        params
      );
      requestCount.add(1);
      seriesListLatency.add(res.timings.duration);
      const ok = check(res, { "series 200": (r) => r.status === 200 });
      errorRate.add(!ok);

      if (ok) {
        try {
          const seriesList = JSON.parse(res.body);
          if (seriesList.length > 0) {
            const series =
              seriesList[Math.floor(Math.random() * seriesList.length)];

            const instRes = http.get(
              `${BASE_URL}/api/v1/studies/${study.id}/series/${series.id}/instances`,
              params
            );
            requestCount.add(1);
            instanceListLatency.add(instRes.timings.duration);
            errorRate.add(instRes.status !== 200);
          }
        } catch (_) {}
      }
    });
  }

  // Presign: request upload URL
  group("presign_upload", () => {
    const studyUid = randomUID();
    const payload = JSON.stringify({
      owner_id: OWNER_ID,
      study_instance_uid: studyUid,
      series_instance_uid: `${studyUid}.1`,
      sop_instance_uid: `${studyUid}.1.${Math.floor(Math.random() * 9999)}`,
      file_size_bytes: Math.floor(Math.random() * 45000000 + 5000000),
    });
    const res = http.post(
      `${BASE_URL}/api/v1/presign/upload`,
      payload,
      params
    );
    requestCount.add(1);
    uploadPresignLatency.add(res.timings.duration);
    const ok = check(res, { "presign 200": (r) => r.status === 200 });
    errorRate.add(!ok);
  });

  sleep(1);
}

// ---------------------------------------------------------------------------
// Stress test scenario (importable)
// ---------------------------------------------------------------------------

export const stressOptions = {
  scenarios: {
    stress_test: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "1m", target: 5 },
        { duration: "2m", target: 10 },
        { duration: "2m", target: 20 },
        { duration: "2m", target: 30 },
        { duration: "1m", target: 0 },
      ],
      gracefulRampDown: "15s",
    },
  },
  thresholds: {
    http_req_duration: ["p(95)<10000"],
    error_rate: ["rate<0.3"],
  },
};