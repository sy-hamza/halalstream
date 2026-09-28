"""Standards-based optional Web Push delivery."""
from __future__ import annotations

import base64
import json
import os
import threading
import math
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


class DailyBudget:
    """Conservative local estimate; the provider's spend limit remains authoritative."""

    def __init__(self, path, limit, reservation, tz, history=()):
        self.path, self.limit, self.reservation, self.tz = Path(path), limit, reservation, tz
        self.lock = threading.RLock()
        self.state = {"days": {}, "reservations": {}}
        if self.path.exists():
            # Fail closed if the ledger is damaged instead of silently resetting it.
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
            for item in self.state["reservations"].values():
                day = item["day"]
                self.state["days"][day] = self.state["days"].get(day, 0) + item["amount"]
            self.state["reservations"] = {}
        else:
            for event in history:
                if event.get("type") != "modal_purify":
                    continue
                try:
                    day = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00")).astimezone(tz).date().isoformat()
                    cost = float(event.get("estimated_cost_usd", 0))
                    if math.isfinite(cost) and cost > 0:
                        self.state["days"][day] = self.state["days"].get(day, 0) + cost
                except (ValueError, TypeError, KeyError):
                    continue
        atomic_json(self.path, self.state)

    def snapshot(self):
        with self.lock:
            now = datetime.now(self.tz)
            day = now.date().isoformat()
            spent = self.state["days"].get(day, 0)
            reserved = sum(item["amount"] for item in self.state["reservations"].values())
            remaining = max(0, self.limit - spent - reserved)
            return {"day": day, "limit_usd": self.limit, "spent_usd": round(spent, 6),
                    "reserved_usd": round(reserved, 6), "remaining_usd": round(remaining, 6),
                    "blocked": remaining + 1e-9 < self.reservation,
                    "resets_at": (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()}

    def reserve(self):
        with self.lock:
            status = self.snapshot()
            if status["blocked"]:
                raise RuntimeError("بلغت التنقية الحد اليومي التقديري. تتجدد الإتاحة بعد منتصف الليل بتوقيت إسطنبول؛ التحميل المباشر متاح.")
            token = uuid.uuid4().hex
            self.state["reservations"][token] = {"day": status["day"], "amount": self.reservation}
            atomic_json(self.path, self.state)
            return token

    def settle(self, token, cost, uncertain=False):
        with self.lock:
            item = self.state["reservations"].pop(token, None)
            if not item:
                return
            cost = max(0, float(cost))
            cost = max(cost, item["amount"]) if uncertain else cost
            self.state["days"][item["day"]] = self.state["days"].get(item["day"], 0) + cost
            atomic_json(self.path, self.state)


class WebPush:
    def __init__(self, state_dir, subject):
        self.state_dir = Path(state_dir)
        self.subject = subject
        self.private_key = None
        self.public_key = None
        self.lock = threading.Lock()

    def config(self):
        try:
            self._keys()
            return {"enabled": True, "public_key": self.public_key}
        except Exception:
            return {"enabled": False, "public_key": None}

    def _keys(self):
        with self.lock:
            if self.private_key:
                return
            import pywebpush  # noqa: F401
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            key_file = self.state_dir / "web-push-key.json"
            configured_key = os.getenv("HALALSTREAM_WEB_PUSH_PRIVATE_KEY", "").strip()
            if configured_key:
                value = {"private_key": configured_key}
                private = serialization.load_der_private_key(base64.b64decode(configured_key), password=None)
            elif key_file.exists():
                value = json.loads(key_file.read_text(encoding="utf-8"))
                private = serialization.load_der_private_key(base64.b64decode(value["private_key"]), password=None)
            else:
                private = ec.generate_private_key(ec.SECP256R1())
                value = {"private_key": base64.b64encode(private.private_bytes(
                    serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())).decode()}
                atomic_json(key_file, value)
                key_file.chmod(0o600)
            self.private_key = value["private_key"]
            public = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
            self.public_key = base64.urlsafe_b64encode(public).rstrip(b"=").decode()

    @staticmethod
    def validate(subscription):
        from cryptography.hazmat.primitives.asymmetric import ec
        endpoint = str(subscription.get("endpoint", ""))
        parsed = urlparse(endpoint)
        host = parsed.hostname or ""
        allowed = host in {"fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"}
        allowed = allowed or host.endswith(".push.services.mozilla.com") or host.endswith(".notify.windows.com") or host.endswith(".push.apple.com")
        if not allowed or parsed.scheme != "https" or parsed.port not in (None, 443) or parsed.username or parsed.password or len(endpoint) > 2048:
            raise ValueError("عنوان خدمة الإشعارات غير مدعوم.")
        keys = subscription.get("keys") or {}
        if not isinstance(keys, dict):
            raise ValueError("بيانات اشتراك الإشعارات غير صالحة.")
        normalized = {}
        for name, length in (("auth", 16), ("p256dh", 65)):
            value = str(keys.get(name, ""))
            if not value or len(value) > 100:
                raise ValueError("بيانات اشتراك الإشعارات غير صالحة.")
            decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
            if len(decoded) != length:
                raise ValueError("بيانات اشتراك الإشعارات غير صالحة.")
            if name == "p256dh":
                ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decoded)
            normalized[name] = value
        return {"endpoint": endpoint, "keys": normalized}

    def send(self, subscription, payload, ttl):
        import requests
        from pywebpush import webpush
        self._keys()

        class NoRedirectSession(requests.Session):
            def request(self, *args, **kwargs):
                kwargs["allow_redirects"] = False
                return super().request(*args, **kwargs)

        with NoRedirectSession() as session:
            session.trust_env = False
            response = webpush(subscription_info=self.validate(subscription), data=json.dumps(payload, ensure_ascii=False),
                               vapid_private_key=self.private_key, vapid_claims={"sub": self.subject},
                               ttl=max(1, int(ttl)), timeout=10, requests_session=session)
            if not 200 <= response.status_code < 300:
                raise RuntimeError("Push gateway rejected delivery")
