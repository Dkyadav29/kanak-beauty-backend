"""
seed_demo: load the demo academy data set (courses, trainers, batches, students, payments) into the database.

    python manage.py seed_demo            # idempotent upsert of the demo data set
    python manage.py seed_demo --reset    # wipe ALL academy data first, then seed
    python manage.py seed_demo --force    # allow running when DEBUG is False

Everything here is DEMO CONTENT with deterministic ids (e.g. ``crs_makeup``,
``usr_student_1``), so running the command twice is a no-op. Dates are relative to today so "upcoming" / "today" views always have data
(one live session starts in 5 minutes and is joinable). Rows are written with the
ORM directly, not through the services, because the demo describes historical state.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.batches.models import AttendanceRecord, Batch, BatchEnrollment, BatchHistoryEvent, LiveSession, Recording
from apps.certificates.models import Certificate, CertificateVerificationLog
from apps.core.models import AuditLog
from apps.courses.models import Course, CourseModule, LearningMaterial, Lesson, Trainer
from apps.enrollments.models import CourseEnrollment, LessonProgress
from apps.payments.models import PaymentTransaction, WebhookEvent
from apps.referrals.models import Referral, ReferralProgramConfig, ReferralReward

ADMIN_EMAIL = "admin@kanakacademy.demo"
ADMIN_PASSWORD = "Admin@123"
STUDENT_PASSWORD = "Student@123"


def _img(photo: str, w: int = 1200) -> str:
    return f"https://images.unsplash.com/{photo}?auto=format&fit=crop&w={w}&q=80"


IMAGES = {
    "makeup": _img("photo-1512496015851-a90fb38ba796"),
    "bridal": _img("photo-1515377905703-c4788e51af15"),
    "hair": _img("photo-1562322140-8baeececf3df"),
    "skincare": _img("photo-1570172619644-dfd03ed5d881"),
    "nails": _img("photo-1604654894610-df63bc536371"),
    "salon": _img("photo-1560066984-138dadb4c035"),
    "trainer1": _img("photo-1494790108377-be9c29b29330", 300),
    "trainer2": _img("photo-1531746020798-e6953c6e8e04", 300),
    "trainer3": _img("photo-1544005313-94ddf0286df2", 300),
    "trainer4": _img("photo-1580489944761-15a19d654956", 300),
}

TRAINERS = [
    (
        "tr_meera",
        "Meera Kapoor",
        "Lead Makeup Educator",
        "Celebrity and bridal makeup artist with 14 years of experience across editorial, film and bridal work.",
        "trainer1",
        ["Bridal Makeup", "HD & Airbrush", "Editorial"],
    ),
    (
        "tr_sana",
        "Sana Qureshi",
        "Senior Hair Stylist & Trainer",
        "Former salon creative director specialising in cutting, styling and hair treatments.",
        "trainer2",
        ["Hair Styling", "Hair Care", "Updos"],
    ),
    (
        "tr_ritika",
        "Ritika Nair",
        "Skincare & Aesthetics Trainer",
        "Certified cosmetologist focused on skin analysis, facials and clinical skincare protocols.",
        "trainer3",
        ["Skincare", "Facials", "Skin Analysis"],
    ),
    (
        "tr_devika",
        "Devika Rao",
        "Nail Artist & Salon Business Coach",
        "Award-winning nail artist who also runs a two-location salon and mentors new salon owners.",
        "trainer4",
        ["Nail Art", "Extensions", "Salon Management"],
    ),
]

# (title, type, duration, summary, is_required)


COURSES = [
    {
        "id": "crs_makeup",
        "slug": "professional-makeup-artistry",
        "title": "Professional Makeup Artistry",
        "short_description": "Master foundation matching, contouring, eye artistry and client consultation for real-world makeup careers.",
        "description": "A comprehensive programme covering skin preparation, colour theory, base work, eye and lip artistry, and client management. Students practise on live models in guided sessions and complete a graded portfolio look.",
        "thumbnail": "makeup",
        "trainer": "tr_meera",
        "duration_weeks": 8,
        "total_hours": 48,
        "level": "beginner",
        "format": "hybrid",
        "price": "34999",
        "discount_price": "29999",
        "featured": True,
        "certificate_enabled": True,
        "certificate_issuance": "automatic",
        "criteria": (True, 75, True),
        "prerequisites": ["No prior experience required", "Own basic brush set (list provided after enrollment)"],
        "outcomes": [
            "Match foundation for all skin tones and undertones",
            "Execute day, evening and HD camera-ready looks",
            "Run a professional client consultation",
            "Build a portfolio of 6 signature looks",
        ],
        "schedule_summary": "Live practical sessions twice a week, recorded theory lessons available anytime.",
        "modules": [
            (
                "Foundations of Makeup",
                "Tools, hygiene, skin prep and colour theory.",
                [
                    ("Welcome & Kit Walkthrough", "video", 18, "Meet your trainer and set up your professional kit."),
                    ("Skin Types & Preparation", "video", 32, "Prepping skin for long-wear makeup."),
                    ("Colour Theory for Artists", "reading", 20, "Undertones, correction and harmony."),
                    ("Live Practical: Base Work", "live", 120, "Foundation matching on live models."),
                ],
            ),
            (
                "Eyes, Brows & Lips",
                "Precision techniques for the features that define a look.",
                [
                    ("Brow Mapping & Shaping", "video", 28, "Measure, map and fill brows naturally."),
                    ("Eye Shapes & Blending", "video", 41, "Cut crease, halo and smoky techniques."),
                    ("Live Practical: Evening Eye Look", "live", 120, "Guided eye artistry on models."),
                    ("Lip Artistry", "video", 24, "Lining, ombre and long-wear application."),
                ],
            ),
            (
                "Client-Ready Looks",
                "Bringing it together for paying clients.",
                [
                    ("Day to Night Transformation", "video", 36, "Build a look that transitions."),
                    ("HD & Camera Makeup", "video", 30, "Finishing for photography and video."),
                    ("Live Practical: Portfolio Shoot", "live", 150, "Final assessed look."),
                    ("Final Assessment", "assessment", 45, "Graded practical and theory assessment."),
                    (
                        "Bonus: Social Media for Artists",
                        "video",
                        22,
                        "Optional lesson on building your presence.",
                        False,
                    ),
                ],
            ),
        ],
    },
    {
        "id": "crs_bridal",
        "slug": "bridal-makeup-masterclass",
        "title": "Bridal Makeup Masterclass",
        "short_description": "Advanced bridal artistry: long-wear techniques, regional bridal styles, trials and wedding-day workflow.",
        "description": "Designed for artists who already know their base work, this intensive live masterclass covers bridal consultation, trial management, traditional and contemporary bridal looks, and on-the-day logistics.",
        "thumbnail": "bridal",
        "trainer": "tr_meera",
        "duration_weeks": 4,
        "total_hours": 32,
        "level": "advanced",
        "format": "live",
        "price": "44999",
        "discount_price": None,
        "featured": True,
        "certificate_enabled": True,
        "certificate_issuance": "manual_approval",
        "criteria": (True, 90, True),
        "prerequisites": [
            "Completion of Professional Makeup Artistry or equivalent experience",
            "Portfolio review on enrollment",
        ],
        "outcomes": [
            "Deliver 12-hour wear bridal looks",
            "Handle bridal trials and contracts",
            "Master 4 regional bridal styles",
            "Manage wedding-day timelines",
        ],
        "schedule_summary": "Four weeks of intensive live studio sessions, three times per week.",
        "modules": [
            (
                "The Bridal Consultation",
                "Trials, contracts and expectation setting.",
                [
                    ("Bridal Client Psychology", "video", 25, "Understanding the bride and her family."),
                    ("Live: Trial Session Workflow", "live", 150, "Running a professional trial."),
                ],
            ),
            (
                "Signature Bridal Looks",
                "Regional and contemporary styles.",
                [
                    ("Live: Classic North Indian Bride", "live", 180, "Traditional heavy bridal look."),
                    ("Live: South Indian Bride", "live", 180, "Temple jewellery-inspired look."),
                    ("Live: Contemporary Minimal Bride", "live", 150, "Soft glam for the modern bride."),
                    ("Live: Reception Glam", "live", 150, "High-impact evening look."),
                ],
            ),
            (
                "Wedding Day Mastery",
                "Logistics and assessment.",
                [
                    ("Wedding-Day Timeline Planning", "reading", 20, "Scheduling the bridal party."),
                    ("Final Bridal Assessment", "assessment", 180, "Full bridal look under time."),
                ],
            ),
        ],
    },
    {
        "id": "crs_hair",
        "slug": "hair-styling-and-hair-care",
        "title": "Hair Styling and Hair Care",
        "short_description": "Blow-dries, curls, braids, updos and professional hair treatments for salon and bridal clients.",
        "description": "From the science of hair to red-carpet updos. Learn sectioning, heat styling, braiding, bridal hairstyles and salon-grade treatments through recorded demonstrations and live feedback sessions.",
        "thumbnail": "hair",
        "trainer": "tr_sana",
        "duration_weeks": 6,
        "total_hours": 36,
        "level": "intermediate",
        "format": "hybrid",
        "price": "27999",
        "discount_price": "24999",
        "featured": True,
        "certificate_enabled": True,
        "certificate_issuance": "automatic",
        "criteria": (True, 60, False),
        "prerequisites": ["Basic familiarity with hair tools recommended"],
        "outcomes": [
            "Perform salon blow-dries and heat styling",
            "Create 5 bridal updos",
            "Recommend treatments by hair type",
        ],
        "schedule_summary": "Recorded demos weekly plus one live practice session per week.",
        "modules": [
            (
                "Hair Science & Tools",
                "Structure, porosity and the professional toolkit.",
                [
                    ("Understanding Hair Structure", "video", 26, "Cuticle, cortex and porosity."),
                    ("Sectioning & Tension", "video", 30, "The foundation of every style."),
                ],
            ),
            (
                "Heat Styling",
                "Blow-dries, curls and waves.",
                [
                    ("Salon Blow-Dry", "video", 38, "Volume and smoothness."),
                    ("Curls, Waves & Finishing", "video", 34, "Tong, wand and iron techniques."),
                    ("Live: Styling Practice", "live", 120, "Guided practice with feedback."),
                ],
            ),
            (
                "Updos & Bridal Hair",
                "Braids, buns and occasion styles.",
                [
                    ("Braiding Fundamentals", "video", 29, "Three to five strand braids."),
                    ("Bridal Updo Series", "video", 45, "Five signature updos."),
                    ("Live: Bridal Hair Practical", "live", 150, "Full bridal hairstyle on a model."),
                ],
            ),
        ],
    },
    {
        "id": "crs_skincare",
        "slug": "skincare-and-facial-techniques",
        "title": "Skincare and Facial Techniques",
        "short_description": "Skin analysis, cleansing protocols, professional facials and treatment planning.",
        "description": "A recorded, self-paced programme covering skin anatomy, analysis, product knowledge and step-by-step professional facial protocols, with downloadable treatment cards.",
        "thumbnail": "skincare",
        "trainer": "tr_ritika",
        "duration_weeks": 5,
        "total_hours": 24,
        "level": "beginner",
        "format": "recorded",
        "price": "18999",
        "discount_price": "14999",
        "featured": True,
        "certificate_enabled": True,
        "certificate_issuance": "automatic",
        "criteria": (True, None, True),
        "prerequisites": ["None"],
        "outcomes": [
            "Perform a structured skin analysis",
            "Deliver 4 professional facial protocols",
            "Build client treatment plans",
        ],
        "schedule_summary": "Self-paced recorded lessons, available 24/7 for 12 months.",
        "modules": [
            (
                "Skin Fundamentals",
                "Anatomy, types and analysis.",
                [
                    ("Skin Anatomy & Physiology", "video", 30, "Layers, functions and ageing."),
                    ("Skin Analysis Method", "video", 28, "Fitzpatrick scale and conditions."),
                    ("Product Knowledge", "reading", 25, "Actives, pH and contraindications."),
                ],
            ),
            (
                "Professional Facials",
                "Step-by-step protocols.",
                [
                    ("Classic Deep Cleansing Facial", "video", 40, "The foundation protocol."),
                    ("Brightening & Anti-Ageing Facials", "video", 42, "Advanced protocols."),
                    ("Facial Massage Techniques", "video", 35, "Lymphatic and lifting massage."),
                    ("Skincare Theory Assessment", "assessment", 30, "Final knowledge check."),
                ],
            ),
        ],
    },
    {
        "id": "crs_nails",
        "slug": "nail-art-and-nail-extensions",
        "title": "Nail Art and Nail Extensions",
        "short_description": "Manicure fundamentals, gel polish, acrylic and gel extensions, and trending nail art.",
        "description": "Hands-on nail programme covering nail anatomy, hygiene, gel systems, acrylic and gel extensions, and a nail-art design library. Includes live technique checks.",
        "thumbnail": "nails",
        "trainer": "tr_devika",
        "duration_weeks": 6,
        "total_hours": 30,
        "level": "beginner",
        "format": "hybrid",
        "price": "22999",
        "discount_price": None,
        "featured": False,
        "certificate_enabled": True,
        "certificate_issuance": "automatic",
        "criteria": (True, 50, False),
        "prerequisites": ["None"],
        "outcomes": [
            "Perform salon manicures and gel polish",
            "Apply acrylic and gel extensions",
            "Create 10 nail-art designs",
        ],
        "schedule_summary": "Recorded lessons plus fortnightly live technique checks.",
        "modules": [
            (
                "Nail Fundamentals",
                "Anatomy, hygiene and manicure.",
                [
                    ("Nail Anatomy & Hygiene", "video", 24, "Safe salon practice."),
                    ("Professional Manicure", "video", 36, "Complete manicure protocol."),
                ],
            ),
            (
                "Extensions & Art",
                "Build and decorate.",
                [
                    ("Gel Polish Application", "video", 30, "Flawless gel polish."),
                    ("Acrylic Extensions", "video", 48, "Sculpting with forms and tips."),
                    ("Live: Technique Check", "live", 90, "Trainer reviews your extensions."),
                    ("Nail Art Library", "video", 40, "Ten trending designs."),
                ],
            ),
        ],
    },
    {
        "id": "crs_salon",
        "slug": "salon-management-and-business-skills",
        "title": "Salon Management and Business Skills",
        "short_description": "Pricing, client retention, hiring, inventory and marketing for beauty professionals and salon owners.",
        "description": "A business programme for beauty professionals who want to run profitable salons or freelance practices. Covers pricing, bookings, retail, staff management and digital marketing.",
        "thumbnail": "salon",
        "trainer": "tr_devika",
        "duration_weeks": 4,
        "total_hours": 16,
        "level": "intermediate",
        "format": "recorded",
        "price": "12999",
        "discount_price": "9999",
        "featured": True,
        "certificate_enabled": True,
        "certificate_issuance": "automatic",
        "criteria": (True, None, False),
        "prerequisites": ["None"],
        "outcomes": [
            "Price services profitably",
            "Design a client retention system",
            "Plan a 90-day salon marketing calendar",
        ],
        "schedule_summary": "Self-paced recorded lessons with downloadable templates.",
        "modules": [
            (
                "Running the Numbers",
                "Pricing, costs and profit.",
                [
                    ("Service Pricing Strategy", "video", 32, "Cost-plus and value pricing."),
                    ("Inventory & Retail", "video", 28, "Stock control and retail upsell."),
                ],
            ),
            (
                "Clients & Growth",
                "Retention and marketing.",
                [
                    ("Client Retention Systems", "video", 30, "Rebooking and loyalty."),
                    ("Digital Marketing for Salons", "video", 35, "Instagram, Google and WhatsApp."),
                    ("Hiring & Managing a Team", "reading", 25, "Roles, incentives and culture."),
                ],
            ),
        ],
    },
    {
        "id": "crs_airbrush",
        "slug": "airbrush-makeup-intensive",
        "title": "Airbrush Makeup Intensive",
        "short_description": "A short intensive on airbrush systems, HD finishes and body art.",
        "description": "Draft course currently being prepared by the academy team.",
        "thumbnail": "makeup",
        "trainer": "tr_meera",
        "duration_weeks": 2,
        "total_hours": 12,
        "level": "advanced",
        "format": "live",
        "price": "15999",
        "discount_price": None,
        "status": "draft",
        "featured": False,
        "certificate_enabled": False,
        "certificate_issuance": "manual_approval",
        "criteria": (True, 100, False),
        "prerequisites": ["Professional Makeup Artistry"],
        "outcomes": ["Operate airbrush systems safely"],
        "schedule_summary": "To be announced.",
        "modules": [
            (
                "Airbrush Basics",
                "Equipment and safety.",
                [("Live: Equipment Setup", "live", 120, "Compressors, guns and products.")],
            )
        ],
    },
]

# (idx, name, email, mobile, created_days_ago, extra)
STUDENTS = [
    (
        1,
        "Priya Sharma",
        "priya@student.demo",
        "+91 98111 22334",
        95,
        {"date_of_birth": date(1998, 4, 12), "address": "14 Rose Villa, Koramangala, Bengaluru 560034"},
    ),
    (2, "Ananya Verma", "ananya@student.demo", "+91 98222 33445", 70, {}),
    (3, "Riya Mehta", "riya.mehta@example.demo", "+91 98333 44556", 160, {}),
    (4, "Neha Joshi", "neha.joshi@example.demo", "+91 98444 55667", 140, {}),
    (5, "Kavya Reddy", "kavya.reddy@example.demo", "+91 98555 66778", 120, {}),
    (6, "Isha Patel", "isha.patel@example.demo", "+91 98666 77889", 110, {}),
    (7, "Simran Kaur", "simran.kaur@example.demo", "+91 98777 88990", 88, {}),
    (8, "Tanvi Desai", "tanvi.desai@example.demo", "+91 98888 99001", 60, {}),
    (9, "Pooja Iyer", "pooja.iyer@example.demo", "+91 98999 00112", 45, {}),
    (10, "Meher Khan", "meher.khan@example.demo", "+91 97000 11223", 30, {}),
    (11, "Aarohi Singh", "aarohi.singh@example.demo", "+91 97111 22334", 12, {}),
    (12, "Zoya Ali", "zoya.ali@example.demo", "+91 97222 33445", 5, {"email_verified": False}),
]

# (id, code, name, course, trainer, start_offset, end_offset, capacity, status, note, created_offset, updated_offset)
BATCHES = [
    (
        "bt_makeup_2",
        "KBA-MK-02",
        "Makeup Artistry - Batch 2",
        "crs_makeup",
        "tr_meera",
        -150,
        -95,
        15,
        "archived",
        "Tue & Thu, 10:00-12:00",
        -170,
        -90,
    ),
    (
        "bt_makeup_3",
        "KBA-MK-03",
        "Makeup Artistry - Batch 3",
        "crs_makeup",
        "tr_meera",
        -35,
        21,
        15,
        "active",
        "Tue & Thu, 10:00-12:00",
        -50,
        -2,
    ),
    (
        "bt_hair_1",
        "KBA-HR-01",
        "Hair Styling - Batch 1",
        "crs_hair",
        "tr_sana",
        -21,
        21,
        12,
        "active",
        "Saturdays, 11:00-13:00",
        -40,
        -5,
    ),
    (
        "bt_bridal_4",
        "KBA-BR-04",
        "Bridal Masterclass - Batch 4",
        "crs_bridal",
        "tr_meera",
        9,
        37,
        10,
        "scheduled",
        "Mon, Wed & Fri, 14:00-17:00",
        -20,
        -1,
    ),
    (
        "bt_nails_2",
        "KBA-NL-02",
        "Nail Art - Batch 2",
        "crs_nails",
        "tr_devika",
        -80,
        -38,
        12,
        "completed",
        "Alternate Wednesdays, 15:00-16:30",
        -100,
        -38,
    ),
    (
        "bt_bridal_3",
        "KBA-BR-03",
        "Bridal Masterclass - Batch 3",
        "crs_bridal",
        "tr_meera",
        -60,
        -32,
        10,
        "cancelled",
        "Cancelled due to trainer availability",
        -75,
        -62,
    ),
]


class Command(BaseCommand):
    help = "Seed the database with the demo academy data set (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete ALL existing academy data before seeding.")
        parser.add_argument("--force", action="store_true", help="Run even when settings.DEBUG is False.")

    # ------------------------------------------------------------------ helpers
    def _at(self, days: int, hour: int = 10, minute: int = 0) -> datetime:
        return self.day_start + timedelta(days=days, hours=hour, minutes=minute)

    def _date(self, days: int) -> date:
        return (self.day_start + timedelta(days=days)).date()

    def _id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}_demo_{self.counter:04d}"

    @staticmethod
    def _upsert(model, pk: str, *, auto: dict | None = None, **fields):
        """update_or_create by primary key, then force auto_now(_add) columns via QuerySet.update()."""
        obj, created = model.objects.update_or_create(pk=pk, defaults=fields)
        if auto:
            model.objects.filter(pk=pk).update(**auto)
            for key, value in auto.items():
                setattr(obj, key, value)
        return obj, created

    # ------------------------------------------------------------------ main
    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError("seed_demo only runs with DEBUG=True (development). Pass --force to override.")
        self.now = timezone.localtime()
        self.day_start = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.counter = 0
        self.created = 0

        with transaction.atomic():
            if options["reset"]:
                self._reset()
            self._seed()

        self._print_summary()

    def _reset(self):
        self.stdout.write("Resetting academy data ...")
        for model in (
            AuditLog,
            CertificateVerificationLog,
            Certificate,
            ReferralReward,
            Referral,
            AttendanceRecord,
            Recording,
            LiveSession,
            BatchHistoryEvent,
            BatchEnrollment,
            LessonProgress,
            CourseEnrollment,
            WebhookEvent,
            PaymentTransaction,
            Batch,
            LearningMaterial,
            Lesson,
            CourseModule,
            Course,
            Trainer,
        ):
            model.objects.all().delete()
        User.objects.all().delete()
        ReferralProgramConfig.objects.all().delete()

    # ------------------------------------------------------------------ seed steps
    def _seed(self):
        at, dt = self._at, self._date
        up = self._upsert

        # Referral programme defaults
        config = ReferralProgramConfig.get()
        config.enabled = True
        config.referrer_reward_amount = Decimal("500")
        config.referee_discount_amount = Decimal("300")
        config.max_credit_per_order = Decimal("2000")
        config.currency = "INR"
        config.save()

        # Trainers
        trainers: dict[str, Trainer] = {}
        for tid, name, title, bio, avatar, specialities in TRAINERS:
            trainers[tid], _ = up(
                Trainer,
                tid,
                name=name,
                title=title,
                bio=bio,
                avatar_url=IMAGES[avatar],
                specialities=specialities,
                is_active=True,
            )

        # Courses, modules, lessons, materials
        courses: dict[str, Course] = {}
        lessons_by_course: dict[str, list[Lesson]] = {}
        for c in COURSES:
            require_all, min_att, require_assessment = c["criteria"]
            courses[c["id"]], _ = up(
                Course,
                c["id"],
                auto={"created_at": at(-120), "updated_at": at(-10)},
                slug=c["slug"],
                title=c["title"],
                short_description=c["short_description"],
                description=c["description"],
                thumbnail_url=IMAGES[c["thumbnail"]],
                trainer=trainers[c["trainer"]],
                duration_weeks=c["duration_weeks"],
                total_hours=c["total_hours"],
                level=c["level"],
                format=c["format"],
                price=Decimal(c["price"]),
                discount_price=Decimal(c["discount_price"]) if c["discount_price"] else None,
                currency="INR",
                status=c.get("status", "published"),
                featured=c["featured"],
                certificate_enabled=c["certificate_enabled"],
                certificate_issuance=c["certificate_issuance"],
                require_all_required_lessons=require_all,
                min_live_attendance_percent=min_att,
                require_assessment=require_assessment,
                prerequisites=c["prerequisites"],
                outcomes=c["outcomes"],
                schedule_summary=c["schedule_summary"],
            )
            lessons_by_course[c["id"]] = []
            for m_order, (m_title, m_desc, m_lessons) in enumerate(c["modules"], start=1):
                module_id = f"{c['id']}_m{m_order}"
                module, _ = up(
                    CourseModule, module_id, course=courses[c["id"]], title=m_title, description=m_desc, order=m_order
                )
                for l_order, spec in enumerate(m_lessons, start=1):
                    title, ltype, duration, summary = spec[:4]
                    required = spec[4] if len(spec) > 4 else True
                    lesson_id = f"{module_id}_l{l_order}"
                    lesson, _ = up(
                        Lesson,
                        lesson_id,
                        module=module,
                        title=title,
                        type=ltype,
                        duration_minutes=duration,
                        order=l_order,
                        is_required=required,
                        summary=summary,
                        video_storage_key=f"lessons/demo/{lesson_id}.mp4" if ltype == "video" else "",
                    )
                    lessons_by_course[c["id"]].append(lesson)
                    if ltype == "video":
                        up(
                            LearningMaterial,
                            f"{lesson_id}_m1",
                            lesson=lesson,
                            title=f"{title} - Reference Notes",
                            type="pdf",
                            storage_key=f"materials/demo/{lesson_id}.pdf",
                            size_label="1.2 MB",
                        )

        # Users
        users: dict[str, User] = {}
        admin = self._user(
            "usr_admin_1",
            "Kanak Admin",
            ADMIN_EMAIL,
            "+91 98000 00001",
            ADMIN_PASSWORD,
            created_at=at(-400, 9),
            role="admin",
            is_staff=True,
            is_superuser=True,
            referral_code="KANAK-ADMIN",
        )
        users[admin.id] = admin
        for idx, name, email, mobile, days_ago, extra in STUDENTS:
            code = f"{name.split(' ')[0].upper()[:6]}-{str(1000 + idx * 37)[-4:]}"
            user = self._user(
                f"usr_student_{idx}",
                name,
                email,
                mobile,
                STUDENT_PASSWORD,
                created_at=at(-days_ago, 9),
                role="student",
                referral_code=code,
                **extra,
            )
            users[user.id] = user

        # Batches
        batches: dict[str, Batch] = {}
        for bid, code, name, course_id, trainer_id, start, end, capacity, status, note, created, updated in BATCHES:
            batches[bid], _ = up(
                Batch,
                bid,
                auto={"created_at": at(created), "updated_at": at(updated)},
                code=code,
                name=name,
                course=courses[course_id],
                trainer=trainers[trainer_id],
                start_date=dt(start),
                end_date=dt(end),
                capacity=capacity,
                status=status,
                schedule_note=note,
            )

        # Purchases / enrollments / progress / certificates
        enrollments: dict[tuple[str, str], CourseEnrollment] = {}
        batch_members: list[BatchEnrollment] = []
        payments: list[PaymentTransaction] = []
        certificates: list[Certificate] = []
        self.order_seq = 1040

        def purchase(
            student_id, course_id, days_ago, *, batch_id=None, status="paid", method=None, refund=None, access=None
        ):
            course = courses[course_id]
            payment_id = self._id("pay")
            paid_at = at(-days_ago, 11, 4) if status in ("paid", "refunded") else None
            payment, _ = up(
                PaymentTransaction,
                payment_id,
                auto={"created_at": at(-days_ago, 11)},
                order_id=f"KBA-ORD-{self.order_seq}",
                student=users[student_id],
                course=course,
                amount=course.effective_price,
                currency="INR",
                list_price=course.effective_price,
                referral_discount=Decimal("0"),
                wallet_credit_applied=Decimal("0"),
                status=status,
                refund_status=refund or ("refunded" if status == "refunded" else "none"),
                method=method or ("" if status == "pending" else "UPI"),
                gateway_provider="" if status == "pending" else "sandbox",
                gateway_reference="" if status == "pending" else f"gw_demo_{payment_id[-4:]}",
                checkout_token=f"chk_demo_{payment_id}",
                invoice_available=status in ("paid", "refunded"),
                paid_at=paid_at,
                failure_reason="Card declined by issuing bank" if status == "failed" else "",
                refunded_at=at(-130, 12) if status == "refunded" else None,
                refund_reason="Withdrawn from batch; refund processed." if status == "refunded" else "",
            )
            self.order_seq += 1
            payments.append(payment)
            if status not in ("paid", "refunded"):
                return None
            enrollment, _ = up(
                CourseEnrollment,
                self._id("enr"),
                auto={"enrolled_at": paid_at},
                student=users[student_id],
                course=course,
                batch=batches[batch_id] if batch_id else None,
                source="purchase",
                access_status=access or ("revoked" if status == "refunded" else "active"),
                payment=payment,
                completed_at=None,
            )
            enrollments[(student_id, course_id)] = enrollment
            if batch_id:
                member, _ = up(
                    BatchEnrollment,
                    self._id("ben"),
                    auto={"enrolled_at": paid_at},
                    batch=batches[batch_id],
                    student=users[student_id],
                    status="enrolled",
                    completed_at=None,
                    withdrawn_at=None,
                )
                batch_members.append(member)
            return enrollment

        def complete(student_id, course_id, fraction=1.0, completed_days_ago=None):
            lessons = lessons_by_course[course_id]
            count = round(len(lessons) * fraction)
            base = completed_days_ago if completed_days_ago is not None else 20
            for i, lesson in enumerate(lessons[:count]):
                when = at(-base - (count - i), 18)
                up(
                    LessonProgress,
                    self._id("lp"),
                    auto={"updated_at": when},
                    student=users[student_id],
                    course=courses[course_id],
                    lesson=lesson,
                    completed=True,
                    completed_at=when,
                    last_position_seconds=lesson.duration_minutes * 60,
                )
            if count < len(lessons):
                nxt = lessons[count]
                up(
                    LessonProgress,
                    self._id("lp"),
                    auto={"updated_at": at(-1, 20)},
                    student=users[student_id],
                    course=courses[course_id],
                    lesson=nxt,
                    completed=False,
                    completed_at=None,
                    last_position_seconds=round(nxt.duration_minutes * 60 / 3),
                )

        def issue(student_id, course_id, batch_id, days_ago, seq, revoked=None):
            student, course = users[student_id], courses[course_id]
            year = at(-days_ago).year
            number = f"{settings.CERTIFICATE_NUMBER_PREFIX}-{year}-{seq:05d}"
            completion = at(-days_ago - 1, 17)
            cert, _ = up(
                Certificate,
                self._id("cert"),
                auto={"issued_at": at(-days_ago, 10), "created_at": at(-days_ago, 10)},
                certificate_number=number,
                student=student,
                student_name=student.name,
                course=course,
                course_title=course.title,
                batch=batches[batch_id] if batch_id else None,
                completion_date=completion,
                issued_by="Kanak Beauty Service",
                issued_by_user=None,
                status="revoked" if revoked else "issued",
                revoked_at=at(-max(1, days_ago - 10), 12) if revoked else None,
                revoked_reason=revoked or "",
                revoked_by=admin if revoked else None,
                reissued_from=None,
            )
            certificates.append(cert)
            enrollment = enrollments.get((student_id, course_id))
            if enrollment is not None:
                enrollment.completed_at = completion
                enrollment.save(update_fields=["completed_at"])
            for member in batch_members:
                if member.student_id == student_id and member.batch_id == batch_id:
                    member.status = "completed"
                    member.completed_at = completion
                    member.save(update_fields=["status", "completed_at"])

        # --- Priya Sharma (primary demo student)
        purchase("usr_student_1", "crs_makeup", 36, batch_id="bt_makeup_3", method="UPI")
        complete("usr_student_1", "crs_makeup", 0.55)
        purchase("usr_student_1", "crs_skincare", 80, method="Card")
        complete("usr_student_1", "crs_skincare", 1, 42)
        issue("usr_student_1", "crs_skincare", None, 41, 118)
        purchase("usr_student_1", "crs_bridal", 6, batch_id="bt_bridal_4", method="Net Banking")
        purchase("usr_student_1", "crs_salon", 2, status="failed", method="Card")
        # --- Ananya Verma
        purchase("usr_student_2", "crs_hair", 22, batch_id="bt_hair_1", method="UPI")
        complete("usr_student_2", "crs_hair", 0.4)
        purchase("usr_student_2", "crs_salon", 1, status="pending")
        # --- Batch 2 (archived) alumni
        for i, sid in enumerate(["usr_student_3", "usr_student_4", "usr_student_5", "usr_student_6"]):
            purchase(sid, "crs_makeup", 155 - i, batch_id="bt_makeup_2", method="Card" if i % 2 else "UPI")
            complete(sid, "crs_makeup", 1, 96 + i)
            issue(
                sid,
                "crs_makeup",
                "bt_makeup_2",
                94 + i,
                70 + i,
                "Issued in error: assessment not completed" if i == 3 else None,
            )
        # withdrawn from batch 2
        purchase("usr_student_7", "crs_makeup", 150, batch_id="bt_makeup_2", status="refunded", method="Card")
        for member in batch_members:
            if member.student_id == users["usr_student_7"].id and member.batch_id == "bt_makeup_2":
                member.status = "withdrawn"
                member.withdrawn_at = at(-130, 12)
                member.save(update_fields=["status", "withdrawn_at"])
        # Batch 3 (active) peers
        for i, sid in enumerate(["usr_student_8", "usr_student_9", "usr_student_10"]):
            purchase(sid, "crs_makeup", 40 - i, batch_id="bt_makeup_3", method="UPI")
            complete(sid, "crs_makeup", 0.3 + i * 0.1)
        # Hair batch peers
        for i, sid in enumerate(["usr_student_5", "usr_student_11"]):
            purchase(sid, "crs_hair", 25 - i, batch_id="bt_hair_1", method="Card")
            complete(sid, "crs_hair", 0.35)
        # Nails batch (completed)
        for i, sid in enumerate(["usr_student_3", "usr_student_9", "usr_student_6"]):
            purchase(sid, "crs_nails", 85 - i, batch_id="bt_nails_2", method="UPI")
            complete(sid, "crs_nails", 1, 40 + i)
            issue(sid, "crs_nails", "bt_nails_2", 39 + i, 100 + i)
        # Recorded course buyers
        purchase("usr_student_4", "crs_skincare", 50, method="UPI")
        complete("usr_student_4", "crs_skincare", 0.7)
        purchase("usr_student_8", "crs_salon", 12, method="UPI")
        complete("usr_student_8", "crs_salon", 1, 3)
        issue("usr_student_8", "crs_salon", None, 2, 121)
        purchase("usr_student_10", "crs_skincare", 8, method="Card")
        purchase("usr_student_11", "crs_salon", 4, status="pending")
        purchase("usr_student_12", "crs_nails", 3, status="failed", method="UPI")
        # Bridal batch 4 (scheduled) additional
        purchase("usr_student_3", "crs_bridal", 5, batch_id="bt_bridal_4", method="Card")
        purchase("usr_student_6", "crs_bridal", 4, batch_id="bt_bridal_4", method="UPI")

        # Mark in-progress batch memberships
        for member in batch_members:
            if member.status != "enrolled":
                continue
            batch = batches[member.batch_id]
            has_progress = LessonProgress.objects.filter(
                student_id=member.student_id, course=batch.course, completed=True
            ).exists()
            if batch.status == "active" and has_progress:
                member.status = "in_progress"
                member.save(update_fields=["status"])

        # --- Live sessions, recordings, attendance
        sessions: list[LiveSession] = []
        lesson_index = {lesson.id: lesson for lessons in lessons_by_course.values() for lesson in lessons}

        def add_session(
            course_id,
            batch_id,
            title,
            trainer_id,
            starts_at,
            duration,
            status,
            description,
            *,
            recording=None,
            lesson_id=None,
            join_window=15,
        ):
            sid = self._id("ses")
            session, _ = up(
                LiveSession,
                sid,
                course=courses[course_id],
                batch=batches[batch_id] if batch_id else None,
                title=title,
                trainer=trainers[trainer_id],
                starts_at=starts_at,
                duration_minutes=duration,
                status=status,
                join_window_minutes_before=join_window,
                description=description,
                provider_meeting_ref=f"demo-room-{sid}",
                lesson=lesson_index.get(lesson_id) if lesson_id else None,
            )
            sessions.append(session)
            if status == "completed" and recording:
                up(
                    Recording,
                    self._id("rec"),
                    session=session,
                    course=courses[course_id],
                    title=title,
                    recorded_at=starts_at,
                    duration_minutes=duration,
                    status=recording,
                    lesson=lesson_index.get(lesson_id) if lesson_id else None,
                    storage_key=f"recordings/demo/{sid}.mp4",
                )
            if status == "completed" and batch_id:
                members = [m for m in batch_members if m.batch_id == batch_id and m.status != "withdrawn"]
                for i, member in enumerate(members):
                    att_status = "absent" if i % 5 == 4 else "late" if i % 7 == 6 else "present"
                    up(
                        AttendanceRecord,
                        self._id("att"),
                        auto={"recorded_at": starts_at},
                        session=session,
                        student_id=member.student_id,
                        status=att_status,
                        recorded_by=None,
                    )
            return session

        # Makeup batch 3: past, today (joinable now), upcoming
        add_session(
            "crs_makeup",
            "bt_makeup_3",
            "Live Practical: Base Work",
            "tr_meera",
            at(-28, 10),
            120,
            "completed",
            "Foundation matching on live models.",
            recording="available",
            lesson_id="crs_makeup_m1_l4",
        )
        add_session(
            "crs_makeup",
            "bt_makeup_3",
            "Live Practical: Evening Eye Look",
            "tr_meera",
            at(-14, 10),
            120,
            "completed",
            "Guided eye artistry on models.",
            recording="available",
            lesson_id="crs_makeup_m2_l3",
        )
        add_session(
            "crs_makeup",
            "bt_makeup_3",
            "Q&A: Kit Building on a Budget",
            "tr_meera",
            at(-7, 10),
            60,
            "completed",
            "Open doubt-clearing session.",
            recording="processing",
        )
        add_session(
            "crs_makeup",
            "bt_makeup_3",
            "Live Practical: Day-to-Night Looks",
            "tr_meera",
            self.now + timedelta(minutes=5),
            120,
            "scheduled",
            "Transform a day look into an evening look on your model.",
            join_window=15,
        )
        add_session(
            "crs_makeup",
            "bt_makeup_3",
            "Live Practical: Portfolio Shoot",
            "tr_meera",
            at(7, 10),
            150,
            "scheduled",
            "Final assessed look with professional photography.",
        )
        add_session(
            "crs_makeup",
            "bt_makeup_3",
            "Final Assessment",
            "tr_meera",
            at(14, 10),
            120,
            "scheduled",
            "Graded practical and theory assessment.",
        )
        # Hair batch 1
        add_session(
            "crs_hair",
            "bt_hair_1",
            "Live: Styling Practice",
            "tr_sana",
            at(-14, 11),
            120,
            "completed",
            "Guided blow-dry and curl practice.",
            recording="available",
            lesson_id="crs_hair_m2_l3",
        )
        add_session(
            "crs_hair",
            "bt_hair_1",
            "Live: Braiding Clinic",
            "tr_sana",
            at(-7, 11),
            90,
            "completed",
            "Braiding fundamentals with feedback.",
            recording="unavailable",
        )
        add_session(
            "crs_hair",
            "bt_hair_1",
            "Live: Bridal Hair Practical",
            "tr_sana",
            at(0, 18, 30),
            150,
            "scheduled",
            "Full bridal hairstyle on a model.",
        )
        add_session(
            "crs_hair",
            "bt_hair_1",
            "Live: Updo Masterclass",
            "tr_sana",
            at(6, 11),
            120,
            "scheduled",
            "Five signature bridal updos.",
        )
        # Bridal batch 4 (scheduled)
        add_session(
            "crs_bridal",
            "bt_bridal_4",
            "Live: Trial Session Workflow",
            "tr_meera",
            at(9, 14),
            150,
            "scheduled",
            "Running a professional bridal trial.",
        )
        add_session(
            "crs_bridal",
            "bt_bridal_4",
            "Live: Classic North Indian Bride",
            "tr_meera",
            at(11, 14),
            180,
            "scheduled",
            "Traditional heavy bridal look.",
        )
        add_session(
            "crs_bridal",
            "bt_bridal_4",
            "Live: South Indian Bride",
            "tr_meera",
            at(13, 14),
            180,
            "scheduled",
            "Temple jewellery-inspired look.",
        )
        # Nails batch 2 (completed)
        add_session(
            "crs_nails",
            "bt_nails_2",
            "Live: Technique Check",
            "tr_devika",
            at(-60, 15),
            90,
            "completed",
            "Extension technique review.",
            recording="available",
            lesson_id="crs_nails_m2_l3",
        )
        add_session(
            "crs_nails",
            "bt_nails_2",
            "Live: Final Showcase",
            "tr_devika",
            at(-40, 15),
            90,
            "completed",
            "Student nail-art showcase.",
            recording="available",
        )
        # Archived makeup batch 2
        add_session(
            "crs_makeup",
            "bt_makeup_2",
            "Live Practical: Base Work",
            "tr_meera",
            at(-140, 10),
            120,
            "completed",
            "Foundation matching on live models.",
            recording="available",
            lesson_id="crs_makeup_m1_l4",
        )
        add_session(
            "crs_makeup",
            "bt_makeup_2",
            "Live Practical: Portfolio Shoot",
            "tr_meera",
            at(-100, 10),
            150,
            "completed",
            "Final assessed look.",
            recording="available",
        )
        # Open webinar (no batch, any enrolled student in Skincare)
        add_session(
            "crs_skincare",
            None,
            "Live Webinar: Monsoon Skincare Protocols",
            "tr_ritika",
            at(3, 19),
            60,
            "scheduled",
            "Seasonal protocol updates for enrolled students.",
        )
        # Cancelled session
        add_session(
            "crs_hair",
            "bt_hair_1",
            "Live: Colour Theory Add-on",
            "tr_sana",
            at(2, 11),
            60,
            "cancelled",
            "Cancelled: rescheduled to next month.",
        )

        # --- Batch history
        def hist(batch_id, htype, description, when, actor_name="Kanak Admin", actor=admin):
            up(
                BatchHistoryEvent,
                self._id("bh"),
                auto={"at": when, "created_at": when},
                batch=batches[batch_id],
                type=htype,
                description=description[:400],
                actor=actor,
                actor_name=actor_name,
            )

        for batch in batches.values():
            hist(batch.id, "batch_created", f"Batch {batch.code} created for {batch.course.title}.", batch.created_at)
        for member in batch_members:
            student = users[member.student_id]
            hist(
                member.batch_id,
                "student_added",
                f"{student.name} enrolled in the batch.",
                member.enrolled_at,
                "System (payment webhook)",
                None,
            )
            if member.status == "withdrawn":
                hist(
                    member.batch_id,
                    "student_withdrawn",
                    f"{student.name} withdrawn from the batch (refund processed).",
                    member.withdrawn_at,
                )
            if member.status == "completed":
                hist(
                    member.batch_id,
                    "student_completed",
                    f"{student.name} completed the batch requirements.",
                    member.completed_at,
                    "System",
                    None,
                )
        for session in sessions:
            if not session.batch_id:
                continue
            days_until = round((session.starts_at - self.now).total_seconds() / 86400)
            hist(
                session.batch_id,
                "session_scheduled",
                f'Session "{session.title}" scheduled.',
                at(-1 + min(-1, days_until - 7)),
            )
            if session.status == "completed":
                hist(
                    session.batch_id,
                    "session_completed",
                    f'Session "{session.title}" completed; attendance recorded.',
                    session.starts_at,
                    "System",
                    None,
                )
            if session.status == "cancelled":
                hist(session.batch_id, "session_cancelled", f'Session "{session.title}" cancelled.', at(-1))
        for cert in certificates:
            if not cert.batch_id:
                continue
            hist(
                cert.batch_id,
                "certificate_issued",
                f"Certificate {cert.certificate_number} issued to {cert.student_name}.",
                cert.issued_at,
                "System",
                None,
            )
            if cert.status == "revoked":
                hist(
                    cert.batch_id,
                    "certificate_revoked",
                    f"Certificate {cert.certificate_number} revoked: {cert.revoked_reason}",
                    cert.revoked_at,
                )
        hist("bt_makeup_2", "status_changed", "Status changed from active to completed.", at(-95, 18))
        hist("bt_makeup_2", "status_changed", "Status changed from completed to archived.", at(-80, 9))
        hist("bt_nails_2", "status_changed", "Status changed from active to completed.", at(-38, 17))
        hist(
            "bt_bridal_3",
            "status_changed",
            "Status changed from scheduled to cancelled. Reason: trainer availability.",
            at(-62, 10),
        )
        hist("bt_makeup_3", "status_changed", "Status changed from scheduled to active.", at(-35, 9))
        hist("bt_hair_1", "status_changed", "Status changed from scheduled to active.", at(-21, 9))

        # --- Audit logs
        def audit(action, entity_type, entity_id, details, when):
            up(
                AuditLog,
                self._id("aud"),
                auto={"at": when, "created_at": when},
                actor=admin,
                actor_name=admin.name,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details=details,
                ip_address=None,
            )

        revoked = next((c for c in certificates if c.status == "revoked"), None)
        refunded = next((p for p in payments if p.status == "refunded"), None)
        audit("course.publish", "course", "crs_makeup", 'Published "Professional Makeup Artistry".', at(-118))
        audit("course.update", "course", "crs_skincare", "Updated pricing: discount price set to 14,999.", at(-60))
        audit("batch.create", "batch", "bt_makeup_3", "Created batch KBA-MK-03.", at(-50))
        audit("batch.status", "batch", "bt_bridal_3", "Cancelled batch KBA-BR-03 (trainer availability).", at(-62))
        audit(
            "certificate.revoke",
            "certificate",
            revoked.id if revoked else "cert",
            "Revoked certificate: issued in error, assessment not completed.",
            at(-84),
        )
        audit("batch.archive", "batch", "bt_makeup_2", "Archived batch KBA-MK-02.", at(-80))
        audit(
            "payment.refund",
            "payment",
            refunded.id if refunded else "pay",
            "Refund processed for withdrawn student.",
            at(-130),
        )
        audit("course.create", "course", "crs_airbrush", 'Created draft course "Airbrush Makeup Intensive".', at(-9))

        # --- Refer & Earn (Priya referred three friends; one converted)
        priya = users["usr_student_1"]

        def refer(referee_id, days_ago, convert_course=None):
            referee = users[referee_id]
            referee.referred_by = priya
            referee.save(update_fields=["referred_by"])
            payment = None
            if convert_course:
                payment = next(
                    (
                        p
                        for p in payments
                        if p.student_id == referee.id and p.course_id == convert_course and p.status == "paid"
                    ),
                    None,
                )
            referral, _ = up(
                Referral,
                self._id("ref"),
                auto={"created_at": at(-days_ago, 12)},
                referrer=priya,
                referee=referee,
                referral_code=priya.referral_code,
                status="rewarded" if payment else "signed_up",
                converted_at=payment.paid_at if payment else None,
                reward_amount=config.referrer_reward_amount if payment else None,
                payment=payment,
            )
            if payment:
                up(
                    ReferralReward,
                    self._id("rwd"),
                    auto={"created_at": payment.paid_at},
                    referral=referral,
                    user=priya,
                    payment=payment,
                    amount=config.referrer_reward_amount,
                    currency="INR",
                    status="credited",
                    note=f"Reward for {referee.name}'s first enrollment ({payment.course.title}).",
                )

        refer("usr_student_10", 30, "crs_skincare")
        refer("usr_student_11", 12)
        refer("usr_student_12", 5)

    def _user(self, pk, name, email, mobile, password, *, created_at, role, referral_code, **extra) -> User:
        fields = {
            "name": name,
            "mobile": mobile,
            "role": role,
            "avatar_url": "",
            "email_verified": extra.pop("email_verified", True),
            "is_active": True,
            "is_staff": extra.pop("is_staff", False),
            "is_superuser": extra.pop("is_superuser", False),
            "date_of_birth": extra.pop("date_of_birth", None),
            "address": extra.pop("address", ""),
            "notify_live_class_reminders": True,
            "notify_course_updates": True,
            "notify_payment_receipts": True,
            "notify_marketing": False,
            "referral_code": referral_code,
        }
        user = User.objects.filter(pk=pk).first() or User.objects.filter(email=email).first()
        if user is None:
            user = User(id=pk, email=email, **fields)
        else:
            for key, value in fields.items():
                setattr(user, key, value)
            user.email = email
        if not user.check_password(password):
            user.set_password(password)
        user.created_at = created_at
        user.save()
        return user

    # ------------------------------------------------------------------ output
    def _print_summary(self):
        counts = [
            ("users", User.objects.count()),
            ("trainers", Trainer.objects.count()),
            ("courses", Course.objects.count()),
            ("lessons", Lesson.objects.count()),
            ("batches", Batch.objects.count()),
            ("batch memberships", BatchEnrollment.objects.count()),
            ("live sessions", LiveSession.objects.count()),
            ("recordings", Recording.objects.count()),
            ("attendance records", AttendanceRecord.objects.count()),
            ("payments", PaymentTransaction.objects.count()),
            ("enrollments", CourseEnrollment.objects.count()),
            ("lesson progress rows", LessonProgress.objects.count()),
            ("certificates", Certificate.objects.count()),
            ("referrals", Referral.objects.count()),
            ("wallet entries", ReferralReward.objects.count()),
            ("batch history events", BatchHistoryEvent.objects.count()),
            ("audit rows", AuditLog.objects.count()),
        ]
        self.stdout.write(self.style.SUCCESS("Demo data seeded."))
        for label, count in counts:
            self.stdout.write(f"  {label:<22} {count}")
        self.stdout.write("")
        self.stdout.write("Demo logins:")
        self.stdout.write(f"  admin    {ADMIN_EMAIL} / {ADMIN_PASSWORD}")
        self.stdout.write(f"  student  priya@student.demo / {STUDENT_PASSWORD}")
        self.stdout.write(f"  student  ananya@student.demo / {STUDENT_PASSWORD}")
        self.stdout.write(f"  (all other *.demo students use {STUDENT_PASSWORD})")
