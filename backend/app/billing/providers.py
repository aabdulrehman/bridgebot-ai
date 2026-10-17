"""
Pluggable payment providers.

Today only `ManualProvider` is active: the user pays via PayPal.Me
(https://paypal.me/BunnyOnMoney/<price>USD) or WhatsApp/bank, submits an upgrade
request with the PayPal transaction id / email, and an admin approves it in the
Master Admin dashboard → `service.approve_upgrade()`.

To add Stripe / Paddle later:
  1. implement `create_checkout()` → return a hosted-checkout URL;
  2. implement `parse_webhook()` → verify the provider signature and return a
     `ProviderEvent(kind="subscription.activated", user_ref=..., plan_id=..., ...)`;
  3. set PAYMENT_PROVIDER=stripe (+ keys) — `/api/billing/checkout` and
     `/api/billing/webhook/{provider}` already route to it, and activation goes
     through the same `apply_plan()` / `record_payment()` used by admins.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

from fastapi import HTTPException, Request, status

from ..config import get_settings
from ..models import Plan, User

settings = get_settings()


@dataclass
class CheckoutResult:
    provider: str
    mode: str                      # "manual" (instructions) | "redirect" (hosted checkout)
    checkout_url: Optional[str] = None
    instructions: Optional[str] = None
    paypal_url: Optional[str] = None


def paypal_me_link(amount_cents: int, currency: str = "USD") -> str:
    """https://paypal.me/<user>/<amount><CUR> — e.g. .../19USD (cents kept only when non-zero)."""
    amount = f"{amount_cents // 100}" if amount_cents % 100 == 0 else f"{amount_cents / 100:.2f}"
    return f"{settings.paypal_me_url.rstrip('/')}/{amount}{currency}"


@dataclass
class ProviderEvent:
    kind: str                      # subscription.activated | subscription.canceled | payment.failed
    user_ref: str                  # our user id (passed as client_reference_id / custom_data)
    plan_id: str
    amount_cents: int
    reference: str
    months: int = 1


class PaymentProvider(Protocol):
    name: str

    async def create_checkout(self, user: User, plan: Plan, months: int) -> CheckoutResult: ...

    async def parse_webhook(self, request: Request) -> ProviderEvent: ...


class ManualProvider:
    """Bank transfer / cash / wallet — admin verifies and approves."""

    name = "manual"

    async def create_checkout(self, user: User, plan: Plan, months: int) -> CheckoutResult:
        cents = plan.price_monthly_cents * months
        return CheckoutResult(
            provider=self.name, mode="manual",
            instructions=f"{settings.manual_payment_instructions}\nAmount: {cents / 100:.2f} {plan.currency} for {months} month(s) of {plan.name}.",
            paypal_url=paypal_me_link(cents, plan.currency) if cents else None,
        )

    async def parse_webhook(self, request: Request) -> ProviderEvent:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Manual provider has no webhooks")


class StripeProvider:
    """Skeleton — fill in with the `stripe` SDK (Checkout Session + webhook signature)."""

    name = "stripe"

    async def create_checkout(self, user: User, plan: Plan, months: int) -> CheckoutResult:
        # import stripe; stripe.api_key = settings.stripe_secret_key
        # session = stripe.checkout.Session.create(mode="subscription", client_reference_id=str(user.id),
        #     line_items=[{"price": <price id for plan.id>, "quantity": 1}], success_url=..., cancel_url=...)
        # return CheckoutResult(provider="stripe", mode="redirect", checkout_url=session.url)
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Stripe is not configured yet")

    async def parse_webhook(self, request: Request) -> ProviderEvent:
        # event = stripe.Webhook.construct_event(await request.body(), request.headers["stripe-signature"], settings.stripe_webhook_secret)
        # map checkout.session.completed / invoice.paid -> ProviderEvent("subscription.activated", ...)
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Stripe is not configured yet")


class PaddleProvider(StripeProvider):
    """Skeleton — Paddle Billing (transaction.completed / subscription.* webhooks)."""

    name = "paddle"


_PROVIDERS: Dict[str, Any] = {"manual": ManualProvider(), "stripe": StripeProvider(), "paddle": PaddleProvider()}


def get_provider(name: Optional[str] = None) -> PaymentProvider:
    key = (name or settings.payment_provider or "manual").lower()
    if key not in _PROVIDERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown payment provider {key}")
    return _PROVIDERS[key]
