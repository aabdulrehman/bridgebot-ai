# Payments

**Live today: manual PayPal.** Users pay at `https://paypal.me/BunnyOnMoney/{price}USD` (or via WhatsApp
+966 56 700 9846), paste the PayPal transaction ID on the Subscription page, and the admin
(Abaudii12@gmail.com) approves it in Master Admin → Upgrade requests. Approval calls
`billing/service.py: approve_upgrade()` → `apply_plan()` + `record_payment()`.

## Plug-in point for Stripe / Paddle (not enabled)

Everything is behind `backend/app/billing/providers.py`:

```python
class PaymentProvider(Protocol):
    name: str
    async def create_checkout(self, user, plan, months) -> CheckoutResult: ...   # hosted checkout URL
    async def parse_webhook(self, request) -> ProviderEvent: ...                  # verify signature → event
```

1. Fill in `StripeProvider` (or `PaddleProvider`): create a Checkout Session / Paddle transaction with
   `client_reference_id = user.id`, and in `parse_webhook` verify the signature
   (`stripe.Webhook.construct_event` / Paddle `Paddle-Signature`) and return
   `ProviderEvent(kind="subscription.activated", user_ref=..., plan_id="PRO", amount_cents=1900, reference=..., months=1)`.
2. Set `PAYMENT_PROVIDER=stripe` (or `paddle`) plus the provider keys as Render environment variables.
3. Point the provider's webhook at `POST /api/billing/webhook/stripe` (or `/paddle`).
   `POST /api/billing/checkout` already returns `mode: "redirect"` + `checkout_url` for redirect providers.

Activation goes through the same `apply_plan()` / `record_payment()` the admin uses, so limits, expiry,
revenue reports and webhooks behave identically. Until then the endpoints return `501 Not Implemented`
and the UI keeps showing the PayPal flow.
