# Backend conventions

These rules keep the ten apps consistent. Read them before adding code.

## Source of truth for behaviour
- `../docs/API_CONTRACTS.md` (repo root `docs/`) is the API contract the React frontend already implements against. Paths, query parameter names, response shapes, status codes and error codes must match it exactly.
- `../src/types/*.ts` are the response/request shapes (camelCase). The business rules (status transitions, eligibility, pricing, webhook idempotency) are specified in `../docs/API_CONTRACTS.md` and enforced in each app's `services.py`.
- Service contracts with fixed signatures live in `apps/enrollments/services.py`, `apps/certificates/services.py`, `apps/referrals/services.py`, `apps/core/services.py`. Implement them in place; do not rename.

## Layout per app
```
apps/<app>/
  models.py        (given; do not change fields without coordinating)
  serializers.py   request/response serializers (snake_case fields -> camelCase on the wire)
  services.py      business rules, transactions, audit calls; views stay thin
  selectors.py     (optional) reusable querysets with select_related/prefetch_related
  filters.py       (optional) django-filter FilterSets with camelCase param names
  views.py         APIView/GenericAPIView/ViewSet; declare permission_classes explicitly
  urls.py          path() entries WITHOUT trailing slash, mounted under /api/v1/
  tests/           pytest modules: test_<area>.py
```

## HTTP and serialization
- Prefix is `/api/v1/`; declare paths relative to it (e.g. `path("student/profile", ...)`). No trailing slashes.
- Wire format is camelCase: the renderer/parser (`djangorestframework-camel-case`) converts automatically. Write serializer fields in snake_case (`access_status` -> `accessStatus`). Query parameters: read with `common.utils.query_param(request, "pageSize")`, which also accepts snake_case and treats `all`/empty as absent.
- Pagination: `common.pagination.PageNumberWithSizePagination` (`page`, `pageSize`) returning `{items,total,page,pageSize}`. Use it on every list that the contract marks paginated; non-paginated lists return a plain array.
- Errors: raise `common.exceptions.ApiError` subclasses (`ValidationFailed`, `ConflictError`, `ForbiddenError`, `NotFoundError`) with the contract's `code` (e.g. `ALREADY_ENROLLED`, `OUTSIDE_JOIN_WINDOW`). Serializer validation errors are converted to `{code:"VALIDATION", message, details}` automatically.
- Ownership failures on student resources return 404 (not 403) so ids cannot be enumerated: filter querysets by `request.user` and call `get_object_or_404`.
- Money fields are `DecimalField`; they serialise as JSON numbers (`COERCE_DECIMAL_TO_STRING=False`).
- Dates are ISO-8601 (DRF default, timezone-aware). Batch `start_date`/`end_date` are plain dates (`YYYY-MM-DD`).

## Authorization
- `permission_classes = [HasPermission]` plus `required_permission = "courses:manage"` (or `required_permissions_by_action` on ViewSets). Student-only endpoints use `[IsStudent]` or `HasPermission` with a `:self` permission. Never rely on `IsAuthenticated` alone for protected business data.
- Public endpoints set `permission_classes = [AllowAny]` and `authentication_classes = []` where no user context is needed, and use `common.throttles.PublicLookupThrottle` for lookups.
- Students never see `provider_meeting_ref`, `storage_key`, `checkout` internals, password hashes, OTP hashes or tokens.

## Business rules
- Multi-step writes run in `transaction.atomic()`; lock rows with `select_for_update()` where double submission matters (payments, certificates, batch capacity).
- Every admin mutation calls `apps.core.services.log_audit(actor=request.user, action="course.publish", entity_type="course", entity_id=obj.id, details="...", request=request)`.
- Batch changes also append a `BatchHistoryEvent`. Nothing is hard-deleted through the API; batches/payments/certificates/enrollments are status-driven.
- Avoid N+1: use `select_related`/`prefetch_related` in querysets used by list serializers. Tests may assert query counts with `django_assert_num_queries`.

## Documentation
- Decorate views with `drf_spectacular.utils.extend_schema` (summary, request/response serializers, `OpenApiParameter` for camelCase query params, tags = app name). The schema must generate without warnings: run `python manage.py spectacular --file /dev/null --validate` (Windows: `--file NUL`).

## Tests
- pytest + pytest-django, fixtures from `conftest.py` (`student_client`, `admin_client`, `course`, `recorded_course`, `batch`, `enrollment`, `joinable_session`, ...).
- Run only your app while iterating: `.venv/Scripts/python.exe -m pytest apps/<app> -q`. Default settings target MariaDB; set `TEST_DB_SQLITE=1` in the environment when MariaDB credentials are not available locally.
- Every endpoint needs: success case, auth required (401), wrong role/permission (403), ownership (404 for other users' records), validation error (400), and the business rule conflicts (409) it defines.

## Tooling
- Interpreter: `backend/.venv/Scripts/python.exe` (do not install new packages; everything needed is in requirements.txt).
- Format/lint before finishing: `.venv/Scripts/python.exe -m black apps common config tests` and `.venv/Scripts/python.exe -m ruff check apps common config tests --fix`.
