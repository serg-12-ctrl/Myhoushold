# myproject/myhousehold/serializers.py
# (допишите к существующим сериализаторам авторизации)

from django.db.models import Sum, Count, Min
from .models import Product, Batch, Operation
from django.utils import timezone

class ProductSerializer(serializers.ModelSerializer):
    current_stock = serializers.SerializerMethodField()
    nearest_expiration = serializers.SerializerMethodField()
    active_batches_count = serializers.SerializerMethodField()
    predicted_end_date = serializers.SerializerMethodField()
    stock_status = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ('id', 'name', 'category', 'default_unit', 'minimum_stock', 
                  'current_stock', 'nearest_expiration', 'active_batches_count', 
                  'predicted_end_date', 'stock_status')
        read_only_fields = ('id',)

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("Название не может быть пустым.")
        return value

    def validate_minimum_stock(self, value):
        if value < 0:
            raise serializers.ValidationError("Минимальный остаток не может быть отрицательным.")
        return value

    def validate(self, data):
        # Требование 5.1: У одного пользователя не должно быть дубликатов
        user = self.context['request'].user
        name = data.get('name')
        if Product.objects.filter(user=user, name__iexact=name).exists():
            raise serializers.ValidationError("Товар с таким названием уже существует у этого пользователя.")
        return data

    # Навешиваем расчет аналитики для списков (Требование 5.2)
    def get_current_stock(self, obj):
        return obj.batches.filter(quantity_remaining__gt=0).aggregate(total=Sum('quantity_remaining'))['total'] or 0

    def get_nearest_expiration(self, obj):
        return obj.batches.filter(quantity_remaining__gt=0, expires_at__isnull=False).aggregate(near=Min('expires_at'))['near']

    def get_active_batches_count(self, obj):
        return obj.batches.filter(quantity_remaining__gt=0).count()

    def get_predicted_end_date(self, obj):
        # Заглушка. Будет рассчитываться в Celery-сервисе на основе Operation-логов
        return None 

    def get_stock_status(self, obj):
        stock = self.get_current_stock(obj)
        if stock == 0:
            return 'out_of_stock'
            
        near_exp = self.get_nearest_expiration(obj)
        if near_exp and near_exp < timezone.now():
            return 'expired'
        if near_exp and (near_exp - timezone.now()).days <= 3:
            return 'expiring_soon'
            
        if stock < obj.minimum_stock:
            return 'low'
        return 'enough'


class BatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Batch
        fields = ('id', 'quantity_initial', 'quantity_remaining', 'purchased_at', 'expires_at', 'storage_location', 'price')
        read_only_fields = ('id', 'quantity_remaining')


class ConsumeSerializer(serializers.Serializer):
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)
    strategy = serializers.ChoiceField(choices=['expires_first', 'oldest_first', 'manual'])
    comment = serializers.CharField(required=False, allow_blank=True)
    batch_id = serializers.IntegerField(required=False, allow_null=True)


class DiscardSerializer(serializers.Serializer):
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)
    reason = serializers.CharField(max_length=100)


class AdjustSerializer(serializers.Serializer):
    actual_quantity = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    comment = serializers.CharField(required=False, allow_blank=True)
