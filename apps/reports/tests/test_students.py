import pytest

pytestmark = pytest.mark.django_db

LIST = "/api/v1/admin/students"


def _rows(client, **params):
    res = client.get(LIST, params)
    assert res.status_code == 200, res.json()
    return res.json()


def test_directory_rows_and_ordering(admin_client, academy, student, other_student, unverified_student):
    body = _rows(admin_client)
    assert set(body) == {"items", "total", "page", "pageSize"}
    assert body["total"] == 3
    ids = [row["id"] for row in body["items"]]
    assert ids == [unverified_student.id, other_student.id, student.id]  # newest first

    by_id = {row["id"]: row for row in body["items"]}
    priya = by_id[student.id]
    assert set(priya) == {
        "id",
        "name",
        "email",
        "mobile",
        "avatarUrl",
        "createdAt",
        "enrolledCourses",
        "completedCourses",
        "certificates",
        "lastPaymentStatus",
        "isActive",
    }
    assert priya["avatarUrl"] is None
    assert priya["enrolledCourses"] == 1 and priya["completedCourses"] == 0 and priya["certificates"] == 0
    assert priya["lastPaymentStatus"] == "paid"
    assert priya["isActive"] is True

    ananya = by_id[other_student.id]
    assert ananya["enrolledCourses"] == 1 and ananya["completedCourses"] == 1 and ananya["certificates"] == 1
    assert ananya["lastPaymentStatus"] == "pending"  # the pending order is the latest payment

    zoya = by_id[unverified_student.id]
    assert zoya["enrolledCourses"] == 0 and zoya["lastPaymentStatus"] == "none"


def test_directory_search(admin_client, academy, student, other_student):
    assert [r["id"] for r in _rows(admin_client, search="priya")["items"]] == [student.id]
    assert [r["id"] for r in _rows(admin_client, search="ANANYA@student")["items"]] == [other_student.id]
    assert [r["id"] for r in _rows(admin_client, search="9811122334")["items"]] == [student.id]
    assert [r["id"] for r in _rows(admin_client, search="98111 22334")["items"]] == [student.id]
    assert _rows(admin_client, search="nobody")["total"] == 0


def test_directory_filters(
    admin_client, academy, student, other_student, unverified_student, course, recorded_course, batch
):
    ids = lambda **p: sorted(r["id"] for r in _rows(admin_client, **p)["items"])  # noqa: E731
    assert ids(courseId=course.id) == [student.id]  # Ananya only has a pending order, no enrollment
    assert ids(courseId=recorded_course.id) == [other_student.id]
    assert ids(batchId=batch.id) == [student.id]
    assert ids(paymentStatus="paid") == [student.id]
    assert ids(paymentStatus="pending") == [other_student.id]
    assert ids(paymentStatus="none") == [unverified_student.id]
    assert ids(paymentStatus="all") == sorted([student.id, other_student.id, unverified_student.id])
    assert ids(enrollmentStatus="active") == [student.id]
    assert ids(enrollmentStatus="completed") == [other_student.id]
    assert ids(enrollmentStatus="none") == [unverified_student.id]
    res = admin_client.get(LIST, {"enrollmentStatus": "bogus"})
    assert res.status_code == 400 and res.json()["code"] == "VALIDATION"


def test_directory_pagination(admin_client, academy):
    page1 = _rows(admin_client, page=1, pageSize=2)
    page2 = _rows(admin_client, page=2, pageSize=2)
    assert page1["total"] == 3 and len(page1["items"]) == 2 and page1["pageSize"] == 2 and page1["page"] == 1
    assert len(page2["items"]) == 1 and page2["page"] == 2
    assert {r["id"] for r in page1["items"]}.isdisjoint({r["id"] for r in page2["items"]})
    assert admin_client.get(LIST, {"page": 9}).status_code == 404


def test_directory_query_count_is_bounded(admin_client, academy, django_assert_max_num_queries):
    with django_assert_max_num_queries(4):
        assert admin_client.get(LIST).status_code == 200


def test_student_detail(
    admin_client, academy, student, course, batch, enrollment, progress_stub, completed_session_with_recording
):
    res = admin_client.get(f"{LIST}/{student.id}")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"profile", "enrollments", "payments", "certificates"}
    profile = body["profile"]
    assert profile == {
        "id": student.id,
        "name": "Priya Sharma",
        "email": "priya@student.test",
        "mobile": "+91 98111 22334",
        "avatarUrl": None,
        "createdAt": profile["createdAt"],
        "emailVerified": True,
        "dateOfBirth": None,
        "address": None,
    }
    raw = res.content.decode().lower()
    assert (
        "password" not in raw
        and "token" not in raw
        and "pbkdf2" not in raw
        and "md5$" not in raw
        and "referral" not in raw
    )

    assert len(body["enrollments"]) == 1
    enr = body["enrollments"][0]
    assert enr == {
        "enrollmentId": enrollment.id,
        "courseId": course.id,
        "courseTitle": course.title,
        "batchName": batch.name,
        "accessStatus": "active",
        "progressPercent": 55,
        "enrolledAt": enr["enrolledAt"],
        "completedAt": None,
        "attendancePercent": 100,
    }
    assert body["payments"] == [
        {
            "id": enrollment.payment.id,
            "orderId": "KBA-ORD-1001",
            "courseTitle": course.title,
            "amount": 29999,
            "currency": "INR",
            "status": "paid",
            "createdAt": body["payments"][0]["createdAt"],
        }
    ]
    assert body["certificates"] == []


def test_student_detail_with_certificate_and_no_sessions(
    admin_client, academy, other_student, recorded_course, progress_stub
):
    body = admin_client.get(f"{LIST}/{other_student.id}").json()
    assert body["enrollments"][0]["attendancePercent"] is None
    assert body["enrollments"][0]["batchName"] is None
    assert body["enrollments"][0]["completedAt"] is not None
    assert len(body["certificates"]) == 1
    cert = body["certificates"][0]
    assert set(cert) == {"id", "certificateNumber", "courseTitle", "issuedAt", "status"}
    assert cert["status"] == "issued" and cert["courseTitle"] == recorded_course.title
    assert [p["status"] for p in body["payments"]] == ["pending", "paid"]


def test_student_detail_not_found_for_admin_or_unknown(admin_client, admin_user, academy, progress_stub):
    assert admin_client.get(f"{LIST}/{admin_user.id}").status_code == 404
    assert admin_client.get(f"{LIST}/usr_missing").status_code == 404


def test_students_permissions(api, student_client, student):
    assert api.get(LIST).status_code == 401
    assert student_client.get(LIST).status_code == 403
    assert student_client.get(f"{LIST}/{student.id}").status_code == 403
