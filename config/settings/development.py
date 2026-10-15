"""Local development settings: verbose errors, relaxed hosts, console email, sandbox gateway."""

from .base import *  # noqa: F401,F403
from .base import REST_FRAMEWORK, config

DEBUG = True
ALLOWED_HOSTS = ["*"]

# JSON only, like production: opening an endpoint in a browser shows the raw
# response instead of DRF's browsable HTML page with its method/form controls.
# Set DRF_BROWSABLE_API=1 in .env to get the browsable page back locally.
if config("DRF_BROWSABLE_API", default=False, cast=bool):
    REST_FRAMEWORK = {
        **REST_FRAMEWORK,
        "DEFAULT_RENDERER_CLASSES": (
            "djangorestframework_camel_case.render.CamelCaseJSONRenderer",
            "djangorestframework_camel_case.render.CamelCaseBrowsableAPIRenderer",
        ),
    }

PAYMENT_GATEWAY = config("PAYMENT_GATEWAY", default="sandbox")
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
