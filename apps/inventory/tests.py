"""Tests del costeo de productos por receta.

Lo que se protege: que el costo de una variante sea la suma exacta de sus
ingredientes, que el editor pueda reemplazar la receta en una llamada sin
dejar filas huerfanas ni mezclar negocios, que el resumen del catalogo
marque lo que aun no esta costeado, y que el precio sugerido siga la regla
de la casa (costo x2 redondeado a miles hacia abajo).
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.business.models import Business
from apps.inventory.costing import costing_figures, suggested_price
from apps.inventory.models import RawMaterial, Recipe, UnitOfMeasure
from apps.products.models import Category, Product, ProductVariant


class PrecioSugeridoTests(TestCase):
    def test_costo_por_dos_redondeado_a_miles_hacia_abajo(self):
        self.assertEqual(suggested_price(Decimal("8300")), Decimal("16000"))
        self.assertEqual(suggested_price(Decimal("12700")), Decimal("25000"))
        self.assertEqual(suggested_price(Decimal("500")), Decimal("1000"))

    def test_sin_costo_no_hay_sugerencia(self):
        self.assertIsNone(suggested_price(Decimal("0")))
        self.assertIsNone(suggested_price(None))

    def test_cifras_derivadas(self):
        figuras = costing_figures(Decimal("16000"), Decimal("8300"), has_recipe=True)
        self.assertEqual(figuras["profit"], Decimal("7700.00"))
        self.assertEqual(figuras["margin_pct"], Decimal("48.1"))
        self.assertEqual(figuras["food_cost_pct"], Decimal("51.9"))
        self.assertEqual(figuras["suggested_price"], Decimal("16000"))

    def test_sin_receta_todo_es_none(self):
        figuras = costing_figures(Decimal("16000"), Decimal("0"), has_recipe=False)
        self.assertIsNone(figuras["cost"])
        self.assertIsNone(figuras["margin_pct"])
        self.assertIsNone(figuras["suggested_price"])


class BaseCosteo(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.negocio = Business.objects.create(name="Local de prueba", slug="prueba")
        cls.otro_negocio = Business.objects.create(name="Otro local", slug="otro")
        gramos = UnitOfMeasure.objects.create(name="Gramos", abbreviation="g")
        unidad = UnitOfMeasure.objects.create(name="Unidad", abbreviation="und")

        cls.papas = RawMaterial.objects.create(
            name="Papas", business=cls.negocio, unit=gramos, cost_per_unit=Decimal("2.00")
        )
        cls.queso = RawMaterial.objects.create(
            name="Queso", business=cls.negocio, unit=gramos, cost_per_unit=Decimal("23.33")
        )
        cls.plato = RawMaterial.objects.create(
            name="Plato", business=cls.negocio, unit=unidad, cost_per_unit=Decimal("1000.00")
        )
        cls.pulpa_ajena = RawMaterial.objects.create(
            name="Pulpa de mango",
            business=cls.otro_negocio,
            unit=gramos,
            cost_per_unit=Decimal("10.00"),
        )

        categoria = Category.objects.create(name="Salchipapas", business=cls.negocio)
        producto = Product.objects.create(
            category=categoria, name="Salchipapa Clásica", description="Base"
        )
        cls.personal = ProductVariant.objects.create(
            product=producto, name="Personal", price=Decimal("16000"), is_default=True
        )
        cls.para_dos = ProductVariant.objects.create(
            product=producto, name="Para 2", price=Decimal("25000")
        )

        cls.admin = User.objects.create(username="admin", role=User.Role.ADMIN)
        cls.empleado = User.objects.create(username="empleado", role=User.Role.EMPLOYEE)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def url_variante(self, variante):
        return f"/api/v1/inventory/recipes/by-variant/{variante.id}/"

    def guardar(self, variante, items):
        return self.client.put(self.url_variante(variante), {"items": items}, format="json")


class RecetaPorVarianteTests(BaseCosteo):
    def test_el_costo_es_la_suma_de_los_ingredientes(self):
        resp = self.guardar(
            self.personal,
            [
                {"raw_material_id": self.papas.id, "quantity": "200"},
                {"raw_material_id": self.queso.id, "quantity": "30", "notes": "derretido"},
                {"raw_material_id": self.plato.id, "quantity": "1"},
            ],
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        # 200*2 + 30*23.33 + 1*1000 = 400 + 699.90 + 1000
        self.assertEqual(resp.data["total_cost"], "2099.90")
        self.assertEqual(resp.data["profit"], "13900.10")
        self.assertEqual(resp.data["profit_margin"], "86.9")
        self.assertEqual(resp.data["food_cost_pct"], "13.1")
        self.assertEqual(resp.data["suggested_price"], "4000")
        self.assertTrue(resp.data["has_recipe"])
        self.assertEqual(resp.data["ingredient_count"], 3)
        notas = {i["raw_material_name"]: i["notes"] for i in resp.data["ingredients"]}
        self.assertEqual(notas["Queso"], "derretido")

    def test_guardar_reemplaza_la_receta_entera(self):
        self.guardar(
            self.personal,
            [
                {"raw_material_id": self.papas.id, "quantity": "200"},
                {"raw_material_id": self.queso.id, "quantity": "30"},
            ],
        )
        resp = self.guardar(
            self.personal, [{"raw_material_id": self.papas.id, "quantity": "250"}]
        )
        self.assertEqual(resp.status_code, 200)
        filas = Recipe.objects.filter(product_variant=self.personal)
        self.assertEqual(filas.count(), 1)
        self.assertEqual(filas.get().quantity, Decimal("250"))
        self.assertEqual(resp.data["total_cost"], "500.00")

    def test_lista_vacia_borra_la_receta(self):
        self.guardar(self.personal, [{"raw_material_id": self.papas.id, "quantity": "200"}])
        resp = self.guardar(self.personal, [])
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["has_recipe"])
        self.assertEqual(resp.data["total_cost"], "0.00")
        self.assertIsNone(resp.data["profit"])
        self.assertEqual(Recipe.objects.filter(product_variant=self.personal).count(), 0)

    def test_rechaza_materia_prima_de_otro_negocio(self):
        resp = self.guardar(
            self.personal, [{"raw_material_id": self.pulpa_ajena.id, "quantity": "100"}]
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("otro negocio", resp.data["items"])
        self.assertEqual(Recipe.objects.count(), 0)

    def test_rechaza_ingrediente_repetido(self):
        resp = self.guardar(
            self.personal,
            [
                {"raw_material_id": self.papas.id, "quantity": "100"},
                {"raw_material_id": self.papas.id, "quantity": "50"},
            ],
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Recipe.objects.count(), 0)

    def test_rechaza_cantidad_cero(self):
        resp = self.guardar(self.personal, [{"raw_material_id": self.papas.id, "quantity": "0"}])
        self.assertEqual(resp.status_code, 400)

    def test_variante_inexistente(self):
        resp = self.client.get("/api/v1/inventory/recipes/by-variant/999999/")
        self.assertEqual(resp.status_code, 404)

    def test_el_costeo_es_solo_para_admin(self):
        self.client.force_authenticate(self.empleado)
        resp = self.client.get(self.url_variante(self.personal))
        self.assertEqual(resp.status_code, 403)
        resp = self.guardar(self.personal, [{"raw_material_id": self.papas.id, "quantity": "1"}])
        self.assertEqual(resp.status_code, 403)


class ResumenDeCosteoTests(BaseCosteo):
    URL = "/api/v1/inventory/recipes/costing-summary/"

    def test_marca_lo_costeado_y_la_cobertura(self):
        self.guardar(
            self.personal,
            [
                {"raw_material_id": self.papas.id, "quantity": "200"},
                {"raw_material_id": self.plato.id, "quantity": "1"},
            ],
        )
        resp = self.client.get(self.URL, {"business": "prueba"})
        self.assertEqual(resp.status_code, 200)

        resumen = resp.data["summary"]
        self.assertEqual(resumen["total_variants"], 2)
        self.assertEqual(resumen["costed_variants"], 1)
        self.assertEqual(resumen["uncosted_variants"], 1)
        self.assertEqual(resumen["coverage_pct"], "50.0")
        self.assertEqual(resumen["target_margin_pct"], "50.00")

        por_variante = {r["variant_id"]: r for r in resp.data["items"]}
        personal = por_variante[self.personal.id]
        self.assertTrue(personal["has_recipe"])
        self.assertEqual(personal["cost"], "1400.00")
        self.assertEqual(personal["margin_pct"], "91.3")
        self.assertEqual(personal["suggested_price"], "2000")
        self.assertEqual(personal["category_name"], "Salchipapas")

        para_dos = por_variante[self.para_dos.id]
        self.assertFalse(para_dos["has_recipe"])
        self.assertIsNone(para_dos["cost"])
        self.assertIsNone(para_dos["margin_pct"])
        self.assertEqual(para_dos["price"], "25000.00")

    def test_cuenta_las_variantes_por_debajo_del_margen_objetivo(self):
        # Costo 9.000 sobre 16.000 -> margen 43,8 %, por debajo del 50 % objetivo
        self.guardar(
            self.personal, [{"raw_material_id": self.papas.id, "quantity": "4500"}]
        )
        resp = self.client.get(self.URL)
        self.assertEqual(resp.data["summary"]["below_target_count"], 1)
        self.assertEqual(resp.data["summary"]["avg_margin_pct"], "43.8")

    def test_filtra_por_negocio_y_excluye_inactivas(self):
        self.para_dos.is_active = False
        self.para_dos.save(update_fields=["is_active"])

        resp = self.client.get(self.URL, {"business": "otro"})
        self.assertEqual(resp.data["summary"]["total_variants"], 0)

        resp = self.client.get(self.URL, {"business": "prueba"})
        self.assertEqual(resp.data["summary"]["total_variants"], 1)

        resp = self.client.get(self.URL, {"business": "prueba", "include_inactive": "1"})
        self.assertEqual(resp.data["summary"]["total_variants"], 2)

    def test_solo_admin(self):
        self.client.force_authenticate(self.empleado)
        self.assertEqual(self.client.get(self.URL).status_code, 403)
