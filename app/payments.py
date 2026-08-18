"""
Lightweight payment provider abstraction.

Every provider must implement initialize() and verify() with this shape,
so checkout/webhook code never calls Paystack (or any provider) directly.
Swapping/adding Flutterwave later means adding one more class here --
no changes needed in checkout or webhook routes.
"""
import requests
from flask import current_app


class PaymentProvider:
    def initialize(self, email, amount_kobo, reference, callback_url):
        raise NotImplementedError

    def verify(self, reference):
        raise NotImplementedError


class PaystackProvider(PaymentProvider):
    BASE_URL = "https://api.paystack.co"

    def _headers(self):
        return {
            "Authorization": f"Bearer {current_app.config['PAYSTACK_SECRET_KEY']}",
            "Content-Type": "application/json",
        }

    def initialize(self, email, amount_kobo, reference, callback_url):
        resp = requests.post(
            f"{self.BASE_URL}/transaction/initialize",
            json={
                "email": email,
                "amount": amount_kobo,
                "reference": reference,
                "callback_url": callback_url,
            },
            headers=self._headers(),
            timeout=15,
        )
        data = resp.json()
        if not data.get("status"):
            raise RuntimeError(f"Paystack initialize failed: {data.get('message')}")
        return {
            "authorization_url": data["data"]["authorization_url"],
            "access_code": data["data"]["access_code"],
            "reference": data["data"]["reference"],
        }

    def verify(self, reference):
        resp = requests.get(
            f"{self.BASE_URL}/transaction/verify/{reference}",
            headers=self._headers(),
            timeout=15,
        )
        data = resp.json()
        if not data.get("status"):
            return {"success": False, "raw": data}
        tx = data["data"]
        return {
            "success": tx.get("status") == "success",
            "amount_kobo": tx.get("amount"),
            "currency": tx.get("currency"),
            "raw": tx,
        }


def get_payment_provider(name="paystack"):
    if name == "paystack":
        return PaystackProvider()
    raise ValueError(f"Unknown payment provider: {name}")
