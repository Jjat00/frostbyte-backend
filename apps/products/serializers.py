from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from apps.business.models import Business
from .models import (
    Category,
    ModifierGroup,
    ModifierOption,
    Product,
    ProductModifierGroup,
    ProductVariant,
)


class ProductVariantSerializer(serializers.ModelSerializer):
    """Serializer para variantes de producto"""

    class Meta:
        model = ProductVariant
        fields = [
            "id",
            "name",
            "sku",
            "price",
            "is_default",
            "is_active",
        ]
        read_only_fields = ["sku"]


class ProductVariantCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer para crear/actualizar variantes de producto"""

    class Meta:
        model = ProductVariant
        fields = [
            "id",
            "product",
            "name",
            "sku",
            "price",
            "is_default",
            "is_active",
        ]
        read_only_fields = ["sku"]

    def validate(self, data):
        """Validar que solo haya una variante por defecto por producto"""
        is_default = data.get('is_default', False)
        product = data.get('product')

        if is_default and product:
            # Si estamos actualizando, excluir el objeto actual
            instance = self.instance
            existing_default = ProductVariant.objects.filter(
                product=product,
                is_default=True
            )
            if instance:
                existing_default = existing_default.exclude(id=instance.id)

            if existing_default.exists():
                raise ValidationError(
                    "Ya existe una variante por defecto para este producto. "
                    "Solo puede haber una variante por defecto por producto."
                )

        return data


# ---------------------------------------------------------------------------
# Modificadores (productos configurables: carnes, salsas, tamaños, bebida, etc.)
# ---------------------------------------------------------------------------


class ModifierOptionSerializer(serializers.ModelSerializer):
    """Opción individual de un grupo (ej: Pollo, Mayonesa)."""

    class Meta:
        model = ModifierOption
        fields = [
            "id",
            "group",
            "name",
            "price_delta",
            "is_default",
            "is_active",
            "display_order",
        ]

    def validate(self, data):
        is_default = data.get("is_default", getattr(self.instance, "is_default", False))
        group = data.get("group") or getattr(self.instance, "group", None)
        if is_default and group and group.max_select:
            others = group.options.filter(is_default=True)
            if self.instance:
                others = others.exclude(pk=self.instance.pk)
            if others.count() + 1 > group.max_select:
                raise ValidationError(
                    "Hay más opciones preseleccionadas que el máximo permitido del grupo."
                )
        return data


class ModifierGroupSerializer(serializers.ModelSerializer):
    """CRUD de grupos de opciones, con sus opciones anidadas (read)."""

    options = ModifierOptionSerializer(many=True, read_only=True)
    business_name = serializers.CharField(source="business.name", read_only=True)
    is_required = serializers.BooleanField(read_only=True)

    class Meta:
        model = ModifierGroup
        fields = [
            "id",
            "business",
            "business_name",
            "name",
            "description",
            "min_select",
            "max_select",
            "is_required",
            "is_active",
            "options",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate(self, data):
        min_select = data.get(
            "min_select", getattr(self.instance, "min_select", 0)
        )
        max_select = data.get(
            "max_select", getattr(self.instance, "max_select", 1)
        )
        if min_select > max_select:
            raise ValidationError(
                "El mínimo a elegir no puede ser mayor que el máximo."
            )
        return data


class ProductModifierGroupSerializer(serializers.ModelSerializer):
    """CRUD de la asociación grupo<->producto (admin)."""

    class Meta:
        model = ProductModifierGroup
        fields = [
            "id",
            "product",
            "group",
            "display_order",
            "min_select_override",
            "max_select_override",
            "is_active",
        ]

    def validate(self, data):
        product = data.get("product") or getattr(self.instance, "product", None)
        group = data.get("group") or getattr(self.instance, "group", None)
        if product and group and product.business_id != group.business_id:
            raise ValidationError(
                "El grupo y el producto deben pertenecer al mismo negocio."
            )
        # Validar que el rango efectivo (con overrides) sea coherente.
        if group:
            min_o = data.get("min_select_override", getattr(self.instance, "min_select_override", None))
            max_o = data.get("max_select_override", getattr(self.instance, "max_select_override", None))
            eff_min = min_o if min_o is not None else group.min_select
            eff_max = max_o if max_o is not None else group.max_select
            if eff_min > eff_max:
                raise ValidationError(
                    "El mínimo efectivo no puede superar el máximo efectivo."
                )
        return data


class ProductModifierGroupReadSerializer(serializers.ModelSerializer):
    """Vista para el menú/cliente: el grupo con sus reglas EFECTIVAS y opciones activas."""

    group_id = serializers.IntegerField(source="group.id", read_only=True)
    name = serializers.CharField(source="group.name", read_only=True)
    description = serializers.CharField(source="group.description", read_only=True)
    min_select = serializers.IntegerField(source="effective_min", read_only=True)
    max_select = serializers.IntegerField(source="effective_max", read_only=True)
    is_required = serializers.SerializerMethodField()
    options = serializers.SerializerMethodField()

    class Meta:
        model = ProductModifierGroup
        fields = [
            "id",
            "group_id",
            "name",
            "description",
            "display_order",
            "min_select",
            "max_select",
            "is_required",
            "options",
        ]

    def get_is_required(self, obj):
        return obj.effective_min > 0

    def get_options(self, obj):
        # Aprovecha el prefetch de opciones activas si esta disponible; si no,
        # cae a una query filtrada.
        if "options" in getattr(obj.group, "_prefetched_objects_cache", {}):
            active = obj.group.options.all()
        else:
            active = obj.group.options.filter(is_active=True).order_by("display_order", "name")
        return ModifierOptionSerializer(active, many=True).data


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------


class ProductSerializer(serializers.ModelSerializer):
    """Serializer para productos con sus variantes y grupos de opciones"""

    variants = ProductVariantSerializer(many=True, read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    business_name = serializers.CharField(source="business.name", read_only=True)
    modifier_groups = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "history",
            "image_url",
            "is_active",
            "is_coming_soon",
            "category",
            "category_name",
            "business",
            "business_name",
            "variants",
            "modifier_groups",
            "created_at",
            "updated_at",
        ]
        # business se deriva de la categoría (ver Product.save()).
        read_only_fields = ["slug", "business", "created_at", "updated_at"]

    def get_modifier_groups(self, obj):
        # Usa el prefetch (active_modifier_links) del ProductViewSet si esta
        # disponible; si no, cae a una query filtrada.
        links = getattr(obj, "active_modifier_links", None)
        if links is None:
            links = list(
                obj.modifier_links.filter(is_active=True)
                .select_related("group")
                .order_by("display_order")
            )
        return ProductModifierGroupReadSerializer(links, many=True).data


class ProductCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer para crear/actualizar productos"""

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "history",
            "image_url",
            "is_active",
            "is_coming_soon",
            "category",
        ]
        # business no se envía: se hereda de la categoría en Product.save().
        read_only_fields = ["slug"]


class ProductListSerializer(serializers.ModelSerializer):
    """Serializer para listado de productos con variantes"""

    category_name = serializers.CharField(source="category.name", read_only=True)
    category_slug = serializers.CharField(source="category.slug", read_only=True)
    business_name = serializers.CharField(source="business.name", read_only=True)
    variants = ProductVariantSerializer(many=True, read_only=True)
    variants_count = serializers.SerializerMethodField()
    is_configurable = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "history",
            "image_url",
            "is_active",
            "is_coming_soon",
            "category",
            "category_name",
            "category_slug",
            "business",
            "business_name",
            "variants",
            "variants_count",
            "is_configurable",
        ]

    def get_variants_count(self, obj):
        return obj.variants.filter(is_active=True).count()

    def get_is_configurable(self, obj):
        cached = getattr(obj, "_is_configurable", None)
        if cached is not None:
            return cached
        return obj.modifier_links.filter(is_active=True).exists()


class CategorySerializer(serializers.ModelSerializer):
    """Serializer para categorías"""

    products_count = serializers.SerializerMethodField()
    business_name = serializers.CharField(source="business.name", read_only=True)

    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "display_order",
            "is_active",
            "show_extras",
            "business",
            "business_name",
            "products_count",
        ]
        read_only_fields = ["slug", "products_count"]

    def get_products_count(self, obj):
        return obj.products.filter(is_active=True).count()


class CategoryCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer para crear/actualizar categorías"""

    # Opcional: si no se envia, la vista asigna el negocio principal
    # (compatibilidad con el front actual que no conoce `business`).
    business = serializers.PrimaryKeyRelatedField(
        queryset=Business.objects.all(), required=False
    )

    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "display_order",
            "is_active",
            "show_extras",
            "business",
        ]
        read_only_fields = ["slug"]


class CategoryDetailSerializer(serializers.ModelSerializer):
    """Serializer para detalle de categoría con sus productos"""

    products = ProductListSerializer(many=True, read_only=True)
    business_name = serializers.CharField(source="business.name", read_only=True)

    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "display_order",
            "is_active",
            "show_extras",
            "business",
            "business_name",
            "products",
        ]
