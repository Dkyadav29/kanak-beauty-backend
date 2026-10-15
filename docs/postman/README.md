# Postman collection

`kanak-api.postman_collection.json` covers every endpoint under `/api/v1` (88 requests, 20 folders), with example bodies, bearer auth and scripts that store tokens automatically.

## Import
1. Postman → Import → drop `kanak-api.postman_collection.json` and one environment file (`kanak-local` or `kanak-uat`).
2. Select the environment. `baseUrl` has no trailing slash and already includes `/api/v1`.
3. Run **Auth → Login** (demo: `admin@kanakacademy.demo / Admin@123` or `priya@student.demo / Student@123` after `python manage.py seed_demo`). The test script saves `accessToken` / `refreshToken`; all protected requests inherit `Bearer {{accessToken}}` from the collection.
4. Fill path variables (`courseId`, `batchId`, `studentId`, ...) from list responses.

## Payments
- **Create order** stores `checkoutToken`, `orderId`, `paymentId`.
- **Sandbox: complete checkout** settles locally (only when `PAYMENT_GATEWAY=sandbox`).
- **Gateway webhook** signs the raw body with HMAC-SHA256 in a pre-request script; set `paymentWebhookSecret` to the backend `PAYMENT_WEBHOOK_SECRET`.

## Regenerate after API changes
```bash
cd backend
python manage.py spectacular --file /tmp/schema.yaml
python scripts/generate_postman.py /tmp/schema.yaml docs/postman/kanak-api.postman_collection.json
```
