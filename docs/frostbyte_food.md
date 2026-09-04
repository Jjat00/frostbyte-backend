# Frostbyte Food — dimensión multi-negocio (backend)

Frostbyte Food (comida rápida, 3er piso) es un **negocio independiente** dentro
del mismo sistema que Frostbyte (bebidas, 2º piso): inventario, gastos,
productos, recetas y estadísticas separadas, pero con menú/carrito unificado
para el cliente y dashboard consolidado para el dueño.

La separación se modela con una **dimensión `Business`**, no con un sistema
aparte. Un solo deploy, una sola base de datos, un solo login.

## Modelo

- **`apps.business.Business`** — `name`, `slug`, `floor`, `color`,
  `display_order`, `is_active`. Sembrados: `frostbyte` (piso 2) y
  `frostbyte-food` (piso 3). `Business.get_main()` devuelve el principal
  (`frostbyte`), usado como default de compatibilidad.
- **FK `business`** (PROTECT) en: `products.Category`, `products.Product`,
  `inventory.RawMaterial`, `inventory.PurchaseOrder`,
  `expenses.OperationalExpense`, `expenses.RecurringExpenseTemplate`.
  `Product.business` se **hereda de su categoría** (no se envía en la API).
  `ExpenseCategory`, `UnitOfMeasure` y las recetas (`inventory.Recipe`) quedan
  compartidas/globales; la receta hereda negocio vía `product_variant.product.business`.
- **Modificadores** (productos configurables) en `apps.products`:
  - `ModifierGroup` — `business`, `name`, `min_select`, `max_select`
    (`min_select>0` = obligatorio; `max_select==1` = selección única).
  - `ModifierOption` — `group`, `name`, `price_delta`, `is_default`, `display_order`.
  - `ProductModifierGroup` — asocia grupo↔producto con `min_select_override` /
    `max_select_override` opcionales (`effective_min` / `effective_max`).

Ejemplo salchipapa: variantes `Personal`/`2 personas`/`3 personas` (precio base) +
grupos `Carnes` (elige 2-3), `Salsas` (elige 2-3 de 8), `Bebida` (0-1). El precio
final = precio de la variante + suma de `price_delta` de las opciones elegidas.

**Un grupo apagado (`ModifierGroup.is_active=False`) no existe para nadie**: ni
en la carta, ni en el detalle del producto, ni para el agente de WhatsApp. Es
cómo se retira del menú una opción que ya no se ofrece sin borrar el histórico
de los pedidos que la usaron. Así están desde el 04/09 `Salsas` (mientras no
haya forma de decir cuáles hay hoy) y `Presentación de la carne` (la carne va
en una sola presentación). Para volver a ofrecerlas basta con reactivar el
grupo: sus opciones y sus asociaciones a productos siguen ahí.

## Endpoints nuevos / cambios

- `GET/POST /api/v1/businesses/` — CRUD de negocios (lectura pública, escritura admin).
- `GET/POST /api/v1/modifier-groups/` — grupos con sus opciones anidadas.
- `GET/POST /api/v1/modifier-options/` — opciones individuales.
- `GET/POST /api/v1/product-modifiers/` — asociaciones grupo↔producto.
- **Filtro `?business=<slug>`** en categorías, productos, modifier-groups,
  raw-materials, purchase-orders, recipes, expenses, recurring.
- `GET /api/v1/products/{slug}/` ahora incluye `business`, `business_name` y
  `modifier_groups` (con reglas efectivas y opciones activas) para el menú.
- **Analytics**: los 5 endpoints aceptan `?business=<slug>` (sin él = consolidado).
  Nuevo `GET /api/v1/analytics/financial/by_business/?months_offset=0` con el
  desglose por negocio + consolidado para el dashboard del dueño. Los ingresos
  por negocio se derivan de `OrderItem → product_variant → product → business`
  (sin tocar `orders`), prorrateando el descuento del pedido por la proporción
  de subtotal de cada negocio para que el consolidado siempre cuadre.

Compatibilidad: lo que se crea sin `business` cae en el negocio principal, así el
flujo actual de Frostbyte no se rompe.

## Pendiente de integración con PEDIDOS (otra rama)

El catálogo y el cálculo de precio están listos; la captura de la selección del
cliente vive en el flujo de pedidos. Contrato sugerido para esa rama:

1. **`OrderItemModifier`** (snapshot): por cada `OrderItem`, guardar las opciones
   elegidas (nombre + `price_delta` congelados, como ya se hace con `unit_price`)
   para que la comanda diga "Salchipapa 2 pers · pollo+cerdo · salsa rosada+ajo+BBQ".
2. **KDS por piso**: filtrar `ActiveOrdersPage` por negocio. El negocio de cada
   ítem se deriva de `order_item.product_variant.product.business` (o se puede
   denormalizar un `business` en `OrderItem` como snapshot — opcional; analytics
   ya funciona sin él).
3. **Atribución de ingresos**: ya resuelta en analytics por la cadena de FKs; no
   requiere cambios en `orders`.
4. **Validación de selección** al crear el pedido: respetar `effective_min` /
   `effective_max` de cada `ProductModifierGroup` activo del producto.

## Migraciones

Patrón nullable → backfill al principal → not-null (atómico). Las migraciones de
backfill son **irreversibles a propósito** (revertir + reaplicar reasignaría todo
al negocio principal y perdería la separación de Food). Para revertir en prod:
backup + migración manual.
