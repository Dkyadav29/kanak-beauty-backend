"""Convert the drf-spectacular OpenAPI schema into a Postman v2.1 collection with example bodies."""
import json
import re
import sys
import uuid

import yaml

schema = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
out = sys.argv[2]
comps = schema["components"]["schemas"]


def resolve(s):
    while "$ref" in s:
        s = comps[s["$ref"].split("/")[-1]]
    return s


EXAMPLE_BY_NAME = {
    "email": "priya@student.demo",
    "password": "Student@123",
    "name": "Priya Sharma",
    "mobile": "+91 98765 43210",
    "otp": "123456",
    "code": "PRIYA10",
    "token": "<token-from-email>",
    "refresh": "{{refreshToken}}",
    "refreshToken": "{{refreshToken}}",
    "reason": "Requested by student via support",
    "courseId": "{{courseId}}",
    "batchId": "{{batchId}}",
    "studentId": "{{studentId}}",
    "trainerId": "{{trainerId}}",
    "lessonId": "{{lessonId}}",
    "sessionId": "{{sessionId}}",
    "enrollmentId": "{{enrollmentId}}",
    "userId": "{{studentId}}",
    "referralCode": "PRIYA10",
    "checkoutToken": "{{checkoutToken}}",
    "orderId": "{{orderId}}",
    "newPassword": "NewPass@123",
    "currentPassword": "Student@123",
    "confirmPassword": "NewPass@123",
    "dateOfBirth": "1998-04-12",
    "address": "12 MG Road, Pune",
    "title": "Sample title",
    "description": "Sample description",
    "slug": "sample-slug",
    "avatarUrl": "",
}


def example(s, name="", depth=0):
    s = resolve(s)
    if "example" in s:
        return s["example"]
    if "default" in s and s["default"] is not None:
        return s["default"]
    if "enum" in s:
        return s["enum"][0]
    if "allOf" in s:
        return example(s["allOf"][0], name, depth)
    if "oneOf" in s:
        return example(s["oneOf"][0], name, depth)
    t = s.get("type")
    if t == "object" or "properties" in s:
        if depth > 6:
            return {}
        return {k: example(v, k, depth + 1) for k, v in s.get("properties", {}).items() if not v.get("readOnly")}
    if t == "array":
        return [example(s.get("items", {}), name, depth + 1)]
    if name in EXAMPLE_BY_NAME:
        return EXAMPLE_BY_NAME[name]
    if t == "string":
        f = s.get("format")
        if f == "date-time":
            return "2026-10-20T10:00:00+05:30"
        if f == "date":
            return "2026-10-20"
        if f == "email":
            return "priya@student.demo"
        if f == "uri":
            return "https://example.com/resource"
        if f == "binary":
            return "<file>"
        if f == "decimal":
            return "4999.00"
        if name.lower().endswith("id"):
            return "{{" + name + "}}"
        return "string"
    if t == "integer":
        return s.get("minimum", 1) if s.get("minimum", 0) > 1 else 1
    if t == "number":
        return 1.0
    if t == "boolean":
        return True
    return None


FOLDERS = [
    ("Health & docs", lambda p, t: p.endswith("/health")),
    ("Auth", lambda p, t: "/auth/" in p),
    ("Public catalogue", lambda p, t: t == "catalogue" or p.endswith("/live-sessions/upcoming") or "/certificates/verify/" in p or "/referrals/validate/" in p),
    ("Payments (checkout, webhook, sandbox)", lambda p, t: p.startswith("/api/v1/payments/")),
    ("Media", lambda p, t: "/media/" in p),
    ("Student / Account", lambda p, t: p.startswith("/api/v1/student/") and t == "accounts"),
    ("Student / Learning", lambda p, t: p.startswith("/api/v1/student/") and t == "enrollments"),
    ("Student / Live sessions & recordings", lambda p, t: p.startswith("/api/v1/student/") and t in ("live-sessions", "recordings")),
    ("Student / Payments", lambda p, t: p.startswith("/api/v1/student/") and t == "payments"),
    ("Student / Certificates", lambda p, t: p.startswith("/api/v1/student/") and t == "certificates"),
    ("Student / Referrals", lambda p, t: p.startswith("/api/v1/student/") and t == "referrals"),
    ("Admin / Overview & reports", lambda p, t: p.startswith("/api/v1/admin/") and t == "reports"),
    ("Admin / Courses & trainers", lambda p, t: p.startswith("/api/v1/admin/") and t == "courses"),
    ("Admin / Batches", lambda p, t: p.startswith("/api/v1/admin/batches")),
    ("Admin / Live sessions & recordings", lambda p, t: p.startswith("/api/v1/admin/live-sessions")),
    ("Admin / Enrollments", lambda p, t: p.startswith("/api/v1/admin/enrollments")),
    ("Admin / Payments", lambda p, t: p.startswith("/api/v1/admin/payments")),
    ("Admin / Certificates", lambda p, t: p.startswith("/api/v1/admin/certificates")),
    ("Admin / Referrals", lambda p, t: p.startswith("/api/v1/admin/referrals")),
    ("Admin / Audit logs", lambda p, t: p.startswith("/api/v1/admin/audit-logs")),
]
folders = {name: [] for name, _ in FOLDERS}
folders["Other"] = []


def humanize(op_id):
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", op_id)
    return s[0].upper() + s[1:]


NAMES = {
    "/api/v1/auth/token/refresh": "Refresh access token",
    "/api/v1/auth/me": "Current user (me)",
    "/api/v1/payments/webhook": "Gateway webhook (HMAC signed)",
    "/api/v1/payments/sandbox/complete": "Sandbox: complete checkout (dev only)",
    "/api/v1/payments/orders": "Create order (checkout)",
}

TOKEN_TEST = """// Save tokens for the rest of the collection
if (pm.response.code === 200 || pm.response.code === 201) {
    const data = pm.response.json();
    const body = data.data || data;
    if (body.accessToken) pm.collectionVariables.set("accessToken", body.accessToken);
    if (body.refreshToken) pm.collectionVariables.set("refreshToken", body.refreshToken);
    if (body.access) pm.collectionVariables.set("accessToken", body.access);
    if (body.refresh) pm.collectionVariables.set("refreshToken", body.refresh);
    if (body.user && body.user.id) pm.collectionVariables.set("userId", body.user.id);
}
pm.test("Status is 2xx", () => pm.expect(pm.response.code).to.be.within(200, 299));"""

ORDER_TEST = """if (pm.response.code === 200 || pm.response.code === 201) {
    const data = pm.response.json(); const body = data.data || data;
    const token = (body.gateway && body.gateway.checkoutToken) || body.checkoutToken;
    if (token) pm.collectionVariables.set("checkoutToken", token);
    if (body.orderId) pm.collectionVariables.set("orderId", body.orderId);
    if (body.paymentId) pm.collectionVariables.set("paymentId", body.paymentId);
}"""

PLAYBACK_TEST = """if (pm.response.code === 200) {
    const data = pm.response.json(); const body = data.data || data;
    const url = body.url || body.streamUrl || body.playbackUrl || "";
    const m = url.match(/media\\/recordings\\/([^?]+)/);
    if (m) pm.collectionVariables.set("mediaToken", m[1]);
}"""

WEBHOOK_PRE = """// HMAC-SHA256 hex over the raw JSON body, same as apps/payments/services.sign_payload
const secret = pm.collectionVariables.get("paymentWebhookSecret") || "";
const raw = pm.request.body ? pm.request.body.raw : "";
const sig = CryptoJS.HmacSHA256(raw, secret).toString(CryptoJS.enc.Hex);
pm.request.headers.upsert({ key: "X-Kanak-Signature", value: sig });"""

NOAUTH_PATHS = {
    "/api/v1/payments/webhook",
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/token/refresh",
    "/api/v1/auth/resend-otp",
    "/api/v1/auth/verify-otp",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
}


BODY_OVERRIDES = {
    ("POST", "/api/v1/payments/webhook"): {
        "eventId": "evt_{{$randomUUID}}", "paymentId": "{{paymentId}}", "status": "paid",
        "gatewayReference": "gw_{{$randomAlphaNumeric}}{{$randomInt}}", "method": "UPI", "failureReason": "",
    },
    ("PUT", "/api/v1/admin/courses/{courseId}/modules"): {
        "modules": [
            {"title": "Module 1: Foundations", "description": "Skin types and prep", "lessons": [
                {"title": "Welcome and tools", "type": "video", "durationMinutes": 20, "isRequired": True, "summary": ""},
                {"title": "Hygiene checklist", "type": "reading", "durationMinutes": 10, "isRequired": True, "summary": ""},
            ]},
            {"id": "<existing module id to keep>", "title": "Module 2: Techniques", "description": "", "lessons": []},
        ]
    },
    ("POST", "/api/v1/admin/batches"): {
        "name": "Oct 2026 Weekend Batch", "courseId": "{{courseId}}", "trainerId": "{{trainerId}}",
        "startDate": "2026-11-01", "endDate": "2026-12-20", "capacity": 25, "scheduleNote": "Sat-Sun 10:00-13:00",
    },
    ("POST", "/api/v1/admin/courses"): {
        "title": "Professional Bridal Makeup", "shortDescription": "Hands-on bridal artistry in 6 weeks",
        "description": "Complete bridal makeup course covering skin prep, HD base, eyes, draping and client handling.",
        "thumbnailUrl": "", "trainerId": "{{trainerId}}", "durationWeeks": 6, "totalHours": 36,
        "level": "beginner", "format": "live", "price": "24999.00", "discountPrice": "19999.00", "status": "draft",
        "featured": False, "certificateEnabled": True, "certificateIssuance": "automatic",
        "completionCriteria": {"requireAllRequiredLessons": True, "minLiveAttendancePercent": 75, "requireAssessment": False},
        "prerequisites": ["Basic makeup kit"], "outcomes": ["Bridal HD base", "Eye artistry", "Saree draping"],
        "scheduleSummary": "Weekends, 3 hours per session",
    },
    ("PUT", "/api/v1/admin/referrals/config"): {
        "enabled": True, "referrerRewardAmount": 500, "refereeDiscountAmount": 500, "maxCreditPerOrder": 2000,
    },
    ("POST", "/api/v1/admin/trainers"): {
        "name": "Kanak Verma", "title": "Lead Bridal Artist", "bio": "12 years in bridal and editorial makeup.",
        "avatarUrl": "", "specialties": ["Bridal", "HD"], "experienceYears": 12,
    },
    ("POST", "/api/v1/auth/register"): {
        "name": "New Student", "email": "new.student@example.com", "mobile": "+91 98765 00000",
        "password": "Student@123", "referralCode": "",
    },
    ("POST", "/api/v1/auth/reset-password"): {"token": "<token-from-reset-email>", "password": "NewPass@123"},
    ("POST", "/api/v1/admin/live-sessions/{sessionId}/recording"): {
        "status": "available", "storageKey": "recordings/2026/10/session.mp4", "durationMinutes": 90,
        "title": "Session recording", "lessonId": None,
    },
}
BODY_OVERRIDES[("PUT", "/api/v1/admin/courses/{courseId}")] = BODY_OVERRIDES[("POST", "/api/v1/admin/courses")]
BODY_OVERRIDES[("PUT", "/api/v1/admin/batches/{batchId}")] = BODY_OVERRIDES[("POST", "/api/v1/admin/batches")]
BODY_OVERRIDES[("PUT", "/api/v1/admin/trainers/{trainerId}")] = BODY_OVERRIDES[("POST", "/api/v1/admin/trainers")]


def build_item(path, method, op):
    tag = (op.get("tags") or ["other"])[0]
    name = NAMES.get(path) or op.get("summary") or humanize(op["operationId"])
    pm_path = re.sub(r"\{(\w+)\}", r":\1", path)
    segments = [seg for seg in pm_path.split("/") if seg]
    rel = segments[2:] if segments[:2] == ["api", "v1"] else segments
    url = {"raw": "{{baseUrl}}/" + "/".join(rel), "host": ["{{baseUrl}}"], "path": rel}
    query, variables = [], []
    for p in op.get("parameters", []):
        sch = p.get("schema", {})
        if p.get("in") == "query":
            val = example(sch, p["name"]) if ("enum" in sch or sch.get("type") != "string") else ""
            query.append(
                {
                    "key": p["name"],
                    "value": str(val),
                    "description": ((p.get("description") or "") + (" (optional)" if not p.get("required") else "")).strip(),
                    "disabled": not p.get("required"),
                }
            )
        elif p.get("in") == "path":
            variables.append({"key": p["name"], "value": "{{" + p["name"] + "}}", "description": p.get("description", "")})
    if query:
        url["query"] = query
    if variables:
        url["variable"] = variables
    headers = [{"key": "Accept", "value": "application/json"}]
    req = {"method": method.upper(), "header": headers, "url": url, "description": op.get("description", "")}
    body = None
    rb = op.get("requestBody", {}).get("content", {})
    if "application/json" in rb:
        ex = example(rb["application/json"]["schema"])
        if (method.upper(), path) in BODY_OVERRIDES:
            ov = BODY_OVERRIDES[(method.upper(), path)]
            keys = set(resolve(rb["application/json"]["schema"]).get("properties", {}))
            ex = {k: v for k, v in ov.items() if k in keys or not keys} if isinstance(ex, dict) else ov
        body = {"mode": "raw", "raw": json.dumps(ex, indent=2), "options": {"raw": {"language": "json"}}}
        headers.append({"key": "Content-Type", "value": "application/json"})
    elif "multipart/form-data" in rb:
        sch = resolve(rb["multipart/form-data"]["schema"])
        fd = []
        for k, v in sch.get("properties", {}).items():
            v = resolve(v)
            if v.get("format") == "binary":
                fd.append({"key": k, "type": "file", "src": ""})
            else:
                fd.append({"key": k, "value": str(example(v, k)), "type": "text"})
        body = {"mode": "formdata", "formdata": fd}
    if body:
        req["body"] = body
    item = {"name": name, "request": req, "response": []}
    events = []
    secure = any("jwtAuth" in s for s in op.get("security", []) or [])
    if not secure or path in NOAUTH_PATHS:
        req["auth"] = {"type": "noauth"}
    if path in ("/api/v1/auth/login", "/api/v1/auth/verify-otp", "/api/v1/auth/token/refresh"):
        events.append({"listen": "test", "script": {"type": "text/javascript", "exec": TOKEN_TEST.split("\n")}})
    if path == "/api/v1/payments/orders":
        events.append({"listen": "test", "script": {"type": "text/javascript", "exec": ORDER_TEST.split("\n")}})
    if path == "/api/v1/payments/webhook":
        events.append({"listen": "prerequest", "script": {"type": "text/javascript", "exec": WEBHOOK_PRE.split("\n")}})
        req["header"].append({"key": "X-Kanak-Signature", "value": "<computed by pre-request script>"})
    if path.endswith("/playback"):
        events.append({"listen": "test", "script": {"type": "text/javascript", "exec": PLAYBACK_TEST.split("\n")}})
    if path.startswith("/api/v1/media/recordings/"):
        req["url"]["variable"] = [{"key": "token", "value": "{{mediaToken}}", "description": "Signed token taken from the playback URL"}]
        req["auth"] = {"type": "noauth"}
    if events:
        item["event"] = events
    for code, resp in op.get("responses", {}).items():
        content = resp.get("content", {})
        if "application/json" in content:
            ex = example(content["application/json"]["schema"])
            item["response"].append(
                {
                    "name": f"{code} example",
                    "originalRequest": {"method": req["method"], "header": [], "url": url},
                    "status": resp.get("description", "") or "OK",
                    "code": int(code) if code.isdigit() else 200,
                    "_postman_previewlanguage": "json",
                    "header": [{"key": "Content-Type", "value": "application/json"}],
                    "body": json.dumps(ex, indent=2),
                }
            )
    return item, tag


for path, ops in schema["paths"].items():
    for method, op in ops.items():
        if method not in ("get", "post", "put", "patch", "delete"):
            continue
        item, tag = build_item(path, method, op)
        for fname, pred in FOLDERS:
            if pred(path, tag):
                folders[fname].append(item)
                break
        else:
            folders["Other"].append(item)


def plain(name, segs):
    return {
        "name": name,
        "request": {"method": "GET", "auth": {"type": "noauth"}, "header": [], "url": {"raw": "{{baseUrl}}/" + "/".join(segs), "host": ["{{baseUrl}}"], "path": segs}},
        "response": [],
    }


folders["Health & docs"] = [plain("Health check", ["health"]), plain("OpenAPI schema (YAML)", ["schema"]), plain("Swagger UI", ["docs"]), plain("ReDoc", ["redoc"])]

auth_order = ["register", "resend-otp", "verify-otp", "login", "me", "token/refresh", "forgot-password", "reset-password", "logout"]
folders["Auth"].sort(key=lambda i: next((n for n, k in enumerate(auth_order) if i["request"]["url"]["raw"].endswith(k)), 99))

DESCRIPTION = """Full collection for the Kanak Beauty Service backend (Django REST Framework).

Base URL: `{{baseUrl}}` (default http://localhost:8000/api/v1). Paths have NO trailing slash.

**Getting started**
1. Run `Auth > Login` with the demo admin or student credentials. Its test script stores `accessToken` and `refreshToken` as collection variables, and every protected request sends `Authorization: Bearer {{accessToken}}` through the collection-level auth.
2. Switch roles by logging in again with a different user.
3. Fill `courseId`, `batchId`, `studentId`, etc. from the list responses.

**Demo logins (after `python manage.py seed_demo`)**
- admin: admin@kanakacademy.demo / Admin@123
- student: priya@student.demo / Student@123
- student: ananya@student.demo / Student@123

**Payments**: `Create order` stores `checkoutToken`; `Sandbox: complete checkout` settles it locally (only when PAYMENT_GATEWAY=sandbox). The gateway webhook request signs its raw body with HMAC-SHA256 using `paymentWebhookSecret` (set it to the backend's PAYMENT_WEBHOOK_SECRET).

**Recordings**: `Student > playback` returns a signed URL; its test script extracts the token into `mediaToken` for the `Media > stream` request.

All responses use camelCase keys. Lists are paginated as `{ items, total, page, pageSize }`. Errors use the shared envelope `{ "error": { "code", "message", "details" } }`."""

collection = {
    "info": {
        "_postman_id": str(uuid.uuid4()),
        "name": "Kanak Beauty Service API",
        "description": DESCRIPTION,
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
    },
    "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{accessToken}}", "type": "string"}]},
    "event": [
        {
            "listen": "test",
            "script": {
                "type": "text/javascript",
                "exec": [
                    "pm.test('Response time < 3s', () => pm.expect(pm.response.responseTime).to.be.below(3000));",
                    "const ct = pm.response.headers.get('Content-Type') || '';",
                    "if (ct.includes('application/json')) { pm.test('Body is JSON', () => pm.response.json()); }",
                ],
            },
        }
    ],
    "variable": [
        {"key": "baseUrl", "value": "http://localhost:8000/api/v1"},
        {"key": "accessToken", "value": ""},
        {"key": "refreshToken", "value": ""},
        {"key": "userId", "value": ""},
        {"key": "adminEmail", "value": "admin@kanakacademy.demo"},
        {"key": "adminPassword", "value": "Admin@123"},
        {"key": "studentEmail", "value": "priya@student.demo"},
        {"key": "studentPassword", "value": "Student@123"},
        {"key": "courseId", "value": ""},
        {"key": "idOrSlug", "value": ""},
        {"key": "lessonId", "value": ""},
        {"key": "batchId", "value": ""},
        {"key": "studentId", "value": ""},
        {"key": "trainerId", "value": ""},
        {"key": "sessionId", "value": ""},
        {"key": "recordingId", "value": ""},
        {"key": "mediaToken", "value": ""},
        {"key": "enrollmentId", "value": ""},
        {"key": "paymentId", "value": ""},
        {"key": "orderId", "value": ""},
        {"key": "checkoutToken", "value": ""},
        {"key": "certificateId", "value": ""},
        {"key": "number", "value": "", "description": "certificate number for public verify"},
        {"key": "code", "value": "PRIYA10", "description": "referral code to validate"},
        {"key": "paymentWebhookSecret", "value": "", "description": "PAYMENT_WEBHOOK_SECRET from backend .env"},
    ],
    "item": [{"name": n, "item": items} for n, items in folders.items() if items],
}
json.dump(collection, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("requests:", sum(len(v) for v in folders.values()), "folders:", [n for n, v in folders.items() if v])
