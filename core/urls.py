from django.urls import path

from core.integrations.fragment.client import FRAGMENT_WEBHOOK
from core.integrations.payhot.client import PAYHOT_WEBHOOK
from core.integrations.paypear.client import PAYPEAR_WEBHOOK
from core.integrations.platega.client import PLATEGA_WEBHOOK
from core.views import payment_webhook, paypear_webhook, payhot_webhook, fragment_webhook, health

urlpatterns = [
    path("webhooks/platega/", payment_webhook, name=PLATEGA_WEBHOOK),
    path("webhooks/paypear/", paypear_webhook, name=PAYPEAR_WEBHOOK),
    path("webhooks/payhot/", payhot_webhook, name=PAYHOT_WEBHOOK),
    path("webhooks/fragment/", fragment_webhook, name=FRAGMENT_WEBHOOK),
    path("health/", health, name="health"),
]
