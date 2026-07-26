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




class OperationHistorySerializer(serializers.ModelSerializer):
    storage_location = serializers.CharField(source='batch.storage_location', read_only=True, default=None)

    class Meta:
        model = Operation
        fields = ('id', 'operation_type', 'quantity', 'comment', 'batch_id', 'storage_location', 'created_at')


class ForecastSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    current_stock = serializers.FloatField()
    average_daily_consumption = serializers.FloatField()
    estimated_days_remaining = serializers.IntegerField(allow_null=True)
    estimated_depletion_date = serializers.DateField(allow_null=True)
    confidence = serializers.CharField()
    based_on_days = serializers.IntegerField()


class RecommendationSerializer(serializers.Serializer):
    type = serializers.CharField() # use_soon, buy, waste_risk, check_stock
    priority = serializers.CharField() # high, medium, low
    product_id = serializers.IntegerField()
    batch_id = serializers.IntegerField(required=False, allow_null=True)
    message = serializers.CharField()
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    recommended_quantity = serializers.FloatField(required=False, allow_null=True)
    expected_unused_quantity = serializers.FloatField(required=False, allow_null=True)



class ShoppingItemSerializer(models.ModelSerializer):
    class Meta:
        model = ShoppingItem
        fields = ('id', 'product_id', 'recommended_quantity', 'reason', 'priority', 'added_automatically', 'created_at')

class ShoppingItemCreateSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)
    priority = serializers.ChoiceField(choices=['high', 'medium', 'low'], default='medium')

class ShoppingCompletePurchaseSerializer(serializers.Serializer):
    # Опциональный блок для одновременного создания партии (9.4)
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    purchased_at = serializers.DateTimeField(required=False)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    storage_location = serializers.CharField(required=False)
    price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)


class NotificationSerializer(models.ModelSerializer):
    class Meta:
        model = Notification
        fields = ('id', 'notification_type', 'product_id', 'batch_id', 'message', 'created_at', 'read_at')



class AnalyticsSerializer(serializers.Serializer):
    period = serializers.DictField()
    total_spent = serializers.FloatField()
    discarded_value = serializers.FloatField()
    waste_percent = serializers.FloatField()
    most_consumed_products = serializers.ListField()
    most_discarded_products = serializers.ListField()
    category_expenses = serializers.DictField()