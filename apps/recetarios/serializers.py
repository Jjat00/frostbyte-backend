from rest_framework import serializers
from .models import (
    RecipeBook,
    RecipeBookStep,
    RecipeBookIngredient,
    RecipeBookImage,
)


# --- Nested serializers (read-only) ---

class RecipeBookStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecipeBookStep
        fields = ["id", "step_number", "instruction", "image_url", "tip"]


class RecipeBookIngredientSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecipeBookIngredient
        fields = ["id", "name", "quantity", "unit", "is_optional"]


class RecipeBookImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecipeBookImage
        fields = ["id", "image_url", "caption", "display_order"]


# --- RecipeBook serializers ---

class RecipeBookListSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)
    product_name = serializers.CharField(source="product.name", read_only=True, default=None)
    steps_count = serializers.SerializerMethodField()
    ingredients_count = serializers.SerializerMethodField()

    class Meta:
        model = RecipeBook
        fields = [
            "id",
            "name",
            "slug",
            "image_url",
            "difficulty",
            "prep_time",
            "servings",
            "category",
            "category_name",
            "product",
            "product_name",
            "is_active",
            "steps_count",
            "ingredients_count",
            "created_at",
        ]

    def get_steps_count(self, obj):
        return obj.steps.count()

    def get_ingredients_count(self, obj):
        return obj.ingredients.count()


class RecipeBookDetailSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)
    product_name = serializers.CharField(source="product.name", read_only=True, default=None)
    product_variant_name = serializers.CharField(
        source="product_variant.name", read_only=True, default=None
    )
    steps = RecipeBookStepSerializer(many=True, read_only=True)
    ingredients = RecipeBookIngredientSerializer(many=True, read_only=True)
    images = RecipeBookImageSerializer(many=True, read_only=True)

    class Meta:
        model = RecipeBook
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "category",
            "category_name",
            "product",
            "product_name",
            "product_variant",
            "product_variant_name",
            "difficulty",
            "prep_time",
            "servings",
            "tips",
            "video_url",
            "image_url",
            "is_active",
            "steps",
            "ingredients",
            "images",
            "created_at",
            "updated_at",
        ]


# --- Nested write serializers ---

class RecipeBookStepWriteSerializer(serializers.Serializer):
    step_number = serializers.IntegerField()
    instruction = serializers.CharField()
    image_url = serializers.URLField(required=False, allow_blank=True, default="")
    tip = serializers.CharField(required=False, allow_blank=True, default="")


class RecipeBookIngredientWriteSerializer(serializers.Serializer):
    name = serializers.CharField()
    quantity = serializers.CharField(required=False, allow_blank=True, default="")
    unit = serializers.CharField(required=False, allow_blank=True, default="")
    is_optional = serializers.BooleanField(required=False, default=False)


class RecipeBookImageWriteSerializer(serializers.Serializer):
    image_url = serializers.URLField()
    caption = serializers.CharField(required=False, allow_blank=True, default="")
    display_order = serializers.IntegerField(required=False, default=0)


class RecipeBookCreateUpdateSerializer(serializers.ModelSerializer):
    steps = RecipeBookStepWriteSerializer(many=True)
    ingredients = RecipeBookIngredientWriteSerializer(many=True)
    images = RecipeBookImageWriteSerializer(many=True, required=False, default=[])

    class Meta:
        model = RecipeBook
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "category",
            "product",
            "product_variant",
            "difficulty",
            "prep_time",
            "servings",
            "tips",
            "video_url",
            "image_url",
            "is_active",
            "steps",
            "ingredients",
            "images",
        ]
        read_only_fields = ["slug"]

    def validate(self, data):
        steps = data.get("steps", [])
        ingredients = data.get("ingredients", [])
        if not steps:
            raise serializers.ValidationError(
                {"steps": "Se requiere al menos un paso de preparación."}
            )
        if not ingredients:
            raise serializers.ValidationError(
                {"ingredients": "Se requiere al menos un ingrediente."}
            )
        return data

    def _create_nested(self, recipe, steps_data, ingredients_data, images_data):
        RecipeBookStep.objects.bulk_create([
            RecipeBookStep(recipe=recipe, **step) for step in steps_data
        ])
        RecipeBookIngredient.objects.bulk_create([
            RecipeBookIngredient(recipe=recipe, **ingredient)
            for ingredient in ingredients_data
        ])
        if images_data:
            RecipeBookImage.objects.bulk_create([
                RecipeBookImage(recipe=recipe, **image) for image in images_data
            ])

    def create(self, validated_data):
        steps_data = validated_data.pop("steps")
        ingredients_data = validated_data.pop("ingredients")
        images_data = validated_data.pop("images", [])

        recipe = RecipeBook.objects.create(**validated_data)
        self._create_nested(recipe, steps_data, ingredients_data, images_data)
        return recipe

    def update(self, instance, validated_data):
        steps_data = validated_data.pop("steps")
        ingredients_data = validated_data.pop("ingredients")
        images_data = validated_data.pop("images", [])

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Replace strategy: delete existing and recreate
        instance.steps.all().delete()
        instance.ingredients.all().delete()
        instance.images.all().delete()
        self._create_nested(instance, steps_data, ingredients_data, images_data)

        return instance
