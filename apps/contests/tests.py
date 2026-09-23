from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Contest, ContestEntry, normalize_instagram_handle

ENTRY = {
    "full_name": "Ana Pérez",
    "phone": "311 555 1234",
    "instagram_handle": "@Ana.Perez",
    "costume": "Bruja",
    "declared_adult": True,
}


class ContestFlowTests(TestCase):
    def setUp(self):
        # La migración de datos ya sembró el de Halloween; se publica y abre
        self.contest = Contest.objects.get(slug="disfraces-halloween-2026")
        self.contest.is_published = True
        self.contest.registrations_open = True
        self.contest.save()

        self.customer = User.objects.create_user(
            username="ana", email="ana@example.com", role=User.Role.CUSTOMER)
        self.employee = User.objects.create_user(
            username="barra", role=User.Role.EMPLOYEE)
        self.admin = User.objects.create_user(
            username="jefe", role=User.Role.ADMIN)
        self.client = APIClient()

    def register(self, user=None, **overrides):
        self.client.force_authenticate(user or self.customer)
        return self.client.post(
            "/api/v1/contests/current/entry/", {**ENTRY, **overrides},
            format="json")

    def test_current_is_public(self):
        res = self.client.get("/api/v1/contests/current/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["entry_fee"], "10000.00")
        self.assertEqual(res.data["min_age"], 18)

    def test_unpublished_contest_is_hidden(self):
        self.contest.is_published = False
        self.contest.save()
        res = self.client.get("/api/v1/contests/current/")
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data)

    def test_register_requires_login(self):
        res = self.client.post(
            "/api/v1/contests/current/entry/", ENTRY, format="json")
        self.assertEqual(res.status_code, 401)

    def test_register_creates_pending_entry(self):
        res = self.register()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["status"], "pending")
        self.assertEqual(res.data["number"], 1)
        self.assertEqual(res.data["instagram_handle"], "ana.perez")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.phone, "311 555 1234")

    def test_minors_cannot_register(self):
        res = self.register(declared_adult=False)
        self.assertEqual(res.status_code, 400)
        self.assertIn("declared_adult", res.data)

    def test_invalid_instagram_handle(self):
        res = self.register(instagram_handle="no vale!")
        self.assertEqual(res.status_code, 400)

    def test_closed_registrations(self):
        self.contest.registrations_open = False
        self.contest.save()
        self.assertEqual(self.register().status_code, 400)

    def test_one_active_entry_per_account(self):
        self.assertEqual(self.register().status_code, 201)
        self.assertEqual(self.register().status_code, 400)

    def test_numbers_are_consecutive(self):
        other = User.objects.create_user(username="beto", role=User.Role.CUSTOMER)
        self.register()
        res = self.register(user=other)
        self.assertEqual(res.data["number"], 2)

    def test_confirmed_only_with_payment_and_instagram(self):
        entry_id = self.register().data["id"]
        url = f"/api/v1/contests/admin/entries/{entry_id}/"
        self.client.force_authenticate(self.employee)

        res = self.client.patch(url, {"paid": True}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["status"], "pending")
        self.assertEqual(res.data["paid_by_name"], "barra")

        res = self.client.patch(url, {"follows_instagram": True}, format="json")
        self.assertEqual(res.data["status"], "confirmed")

        res = self.client.patch(url, {"paid": False}, format="json")
        self.assertEqual(res.data["status"], "pending")
        self.assertIsNone(res.data["paid_by_name"])

    def test_customer_cannot_use_staff_endpoints(self):
        entry_id = self.register().data["id"]
        res = self.client.patch(
            f"/api/v1/contests/admin/entries/{entry_id}/", {"paid": True},
            format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.client.get("/api/v1/contests/admin/").status_code, 403)

    def test_cancel_then_register_again(self):
        self.register()
        res = self.client.post("/api/v1/contests/current/entry/cancel/")
        self.assertEqual(res.status_code, 204)
        res = self.register()
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["number"], 2)

    def test_paid_entry_cannot_be_self_cancelled(self):
        entry = ContestEntry.objects.get(pk=self.register().data["id"])
        entry.set_paid(True, self.employee)
        entry.save()
        res = self.client.post("/api/v1/contests/current/entry/cancel/")
        self.assertEqual(res.status_code, 400)

    def test_only_admin_edits_contest(self):
        self.client.force_authenticate(self.employee)
        res = self.client.patch(
            "/api/v1/contests/admin/contest/", {"registrations_open": False},
            format="json")
        self.assertEqual(res.status_code, 403)
        self.client.force_authenticate(self.admin)
        res = self.client.patch(
            "/api/v1/contests/admin/contest/", {"prize": "Botella"},
            format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["prize"], "Botella")

    def test_staff_overview_counts(self):
        self.register()
        self.client.force_authenticate(self.employee)
        res = self.client.get("/api/v1/contests/admin/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["counts"]["pending"], 1)
        self.assertEqual(len(res.data["entries"]), 1)


class NormalizeHandleTests(TestCase):
    def test_variants(self):
        self.assertEqual(normalize_instagram_handle(" @Frostbyte.Col "), "frostbyte.col")
        self.assertEqual(
            normalize_instagram_handle("https://www.instagram.com/frostbyte.col/"),
            "frostbyte.col")
