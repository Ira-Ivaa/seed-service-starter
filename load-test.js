import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  stages: [
    { duration: "5s", target: 10 },
    { duration: "5s", target: 10 },
    { duration: "3s", target: 0 },
  ],
  thresholds: {
    http_req_duration: ["p(95)<1"],
    http_req_failed: ["rate<0.01"],
  },
};

export default function () {
  const res = http.get("http://localhost:8000/ready");
  check(res, {
    "статус 200": (r) => r.status === 200,
  });
  sleep(1);
}
