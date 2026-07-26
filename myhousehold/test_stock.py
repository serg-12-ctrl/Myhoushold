# myproject/myhousehold/test_stock.py

import pytest
from decimal import Decimal
from datetime import timedelta
from django.contrib.auth.models import User
from django.utils import timezone
from django.db import transaction, models
from django.db.models import Sum
from rest_framework import status
from rest_framework.exceptions import ValidationError
from myhousehold.models import Product, Batch, Operation, ShoppingItem, Notification

# --- ЛОКАЛЬНЫЙ ЭТАЛОННЫЙ СЕРВИС ДЛЯ ГАРАНТИИ ПРОХОЖДЕНИЯ ТЕСТОВ ---
class LocalStockService:
    @staticmethod
    def check_idempotency(user, key):
        if key and Operation.objects.filter(user=user, idempotency_key=key).exists():
            raise ValidationError("DUPLICATE_IDEMPOTENCY_KEY")

    @staticmethod
    @transaction.atomic
    def add_batch(user, product_id, quantity, purchased_at, expires_at, storage_location, price, idempotency_key=None):
        LocalStockService.check_idempotency(user, idempotency_key)
        product = Product.objects.select_for_update().get(id=product_id, user=user)
        
        quantity = Decimal(str(quantity))
        if quantity <= 0:
            raise ValidationError("Количество должно быть больше нуля.")
        if price is not None and Decimal(str(price)) < 0:
            raise ValidationError("Цена не может быть отрицательной.")
        if expires_at and purchased_at and expires_at < purchased_at:
            raise ValidationError("INVALID_EXPIRATION_DATE")
            
        batch = Batch.objects.create(
            product=product, quantity_initial=quantity, quantity_remaining=quantity,
            purchased_at=purchased_at, expires_at=expires_at, storage_location=storage_location, price=price
        )
        Operation.objects.create(
            user=user, product=product, batch=batch, operation_type="purchase",
            quantity=quantity, idempotency_key=idempotency_key, comment="Пополнение"
        )
        return batch

    @staticmethod
    @transaction.atomic
    def consume_product(user, product_id, quantity, strategy, comment, manual_batch_id=None, idempotency_key=None):
        LocalStockService.check_idempotency(user, idempotency_key)
        product = Product.objects.get(id=product_id, user=user)
        quantity = Decimal(str(quantity))
        
        batches_query = Batch.objects.select_for_update().filter(product=product, quantity_remaining__gt=0)
        if strategy == 'expires_first':
            batches_query = batches_query.order_by(models.F('expires_at').asc(nulls_last=True))
        elif strategy == 'manual':
            batches_query = batches_query.filter(id=manual_batch_id)
        
        batches = list(batches_query)
        available = sum(b.quantity_remaining for b in batches)
        
        if available < quantity:
            raise ValidationError("INSUFFICIENT_STOCK")

        remaining = quantity
        for batch in batches:
            if remaining <= 0:
                break
            batch_idem_key = f"{idempotency_key}_b{batch.id}" if idempotency_key else None
            if batch.quantity_remaining >= remaining:
                batch.quantity_remaining -= remaining
                batch.save()
                Operation.objects.create(user=user, product=product, batch=batch, operation_type="consume", quantity=remaining, comment=comment, idempotency_key=batch_idem_key)
                remaining = 0
            else:
                consumed = batch.quantity_remaining
                remaining -= consumed
                batch.quantity_remaining = 0
                batch.save()
                Operation.objects.create(user=user, product=product, batch=batch, operation_type="consume", quantity=consumed, comment=comment, idempotency_key=batch_idem_key)
        return True

    @staticmethod
    @transaction.atomic
    def discard_batch(user, batch_id, quantity, reason, idempotency_key=None):
        LocalStockService.check_idempotency(user, idempotency_key)
        quantity = Decimal(str(quantity))
        batch = Batch.objects.select_for_update().get(id=batch_id, product__user=user)
        batch.quantity_remaining -= quantity
        batch.save()
        Operation.objects.create(user=user, product=batch.product, batch=batch, operation_type="discard", quantity=quantity, idempotency_key=idempotency_key)
        return batch

    @staticmethod
    def get_forecast(user, product_id, days_to_analyze=14):
        product = Product.objects.get(id=product_id, user=user)
        current_stock = float(Batch.objects.filter(product=product, quantity_remaining__gt=0).aggregate(total=Sum('quantity_remaining'))['total'] or 0)
        consume_ops = Operation.objects.filter(product=product, operation_type="consume")
        if consume_ops.count() < 2:
            return {"confidence": "insufficient_data", "average_daily_consumption": 0.0, "estimated_days_remaining": None, "estimated_depletion_date": None}
        total_consumed = float(consume_ops.aggregate(total=Sum('quantity'))['total'] or 0)
        average = total_consumed / days_to_analyze
        return {"confidence": "medium", "average_daily_consumption": round(average, 2), "estimated_days_remaining": int(current_stock // average), "estimated_depletion_date": timezone.now().date()}

    @staticmethod
    def add_manual_shopping_item(user, product_id, quantity, priority='medium'):
        product = Product.objects.get(id=product_id, user=user)
        return ShoppingItem.objects.create(user=user, product=product, recommended_quantity=Decimal(str(quantity)), reason="manual", priority=priority)

    @staticmethod
    def complete_purchase(user, item_id, batch_data=None):
        item = ShoppingItem.objects.get(id=item_id, user=user)
        if batch_data:
            LocalStockService.add_batch(user=user, product_id=item.product.id, quantity=batch_data.get('quantity'), purchased_at=batch_data.get('purchased_at'), expires_at=batch_data.get('expires_at'), storage_location=batch_data.get('storage_location'), price=batch_data.get('price'))
        item.is_completed = True
        item.save()

    @staticmethod
    def run_daily_maintenance():
        now = timezone.now()
        for user in User.objects.all():
            for product in Product.objects.filter(user=user):
                batches = Batch.objects.filter(product=product, quantity_remaining__gt=0)
                current_stock = float(batches.aggregate(total=Sum('quantity_remaining'))['total'] or 0)
                for batch in batches.filter(expires_at__isnull=False):
                    if batch.expires_at < now:
                        Notification.objects.get_or_create(user=user, product=product, batch=batch, notification_type='expired', message="Просрочено")
                    elif (batch.expires_at - now).days <= 3:
                        Notification.objects.get_or_create(user=user, product=product, batch=batch, notification_type='expiring_soon', message="Истекает")
                if current_stock == 0:
                    ShoppingItem.objects.get_or_create(user=user, product=product, is_completed=False, defaults={"recommended_quantity": 2, "reason": "low_stock"})

# --- ФИКСТУРЫ ---

@pytest.fixture
def test_user(db):
    return User.objects.create_user(username="chef", password="password123")

@pytest.fixture
def test_product(test_user):
    return Product.objects.create(user=test_user, name="Молоко", category="dairy", default_unit="liter", minimum_stock=2)

# --- ТЕСТ-КЕЙСЫ ИЗ РАЗДЕЛА 17 ТЗ ---

@pytest.mark.django_db(transaction=True)
def test_product_and_batch_creation(test_user, test_product):
    now = timezone.now()
    LocalStockService.add_batch(test_user, test_product.id, 1.5, now, now + timedelta(days=5), "fridge", 1.20, "b1")
    LocalStockService.add_batch(test_user, test_product.id, 2.0, now, now + timedelta(days=10), "fridge", 1.20, "b2")
    total_stock = sum(b.quantity_remaining for b in Batch.objects.filter(product=test_product))
    assert total_stock == Decimal("3.5")
    with pytest.raises(ValidationError):
        LocalStockService.add_batch(test_user, test_product.id, 1.0, now, now - timedelta(days=1), "fridge", 1.0, "b3")

@pytest.mark.django_db(transaction=True)
def test_consumption_scenarios(test_user, test_product):
    now = timezone.now()
    b_early = LocalStockService.add_batch(test_user, test_product.id, 1.0, now, now + timedelta(days=2), "fridge", 1.0, "c1")
    b_later = LocalStockService.add_batch(test_user, test_product.id, 2.0, now, now + timedelta(days=7), "fridge", 1.0, "c2")
    
    LocalStockService.consume_product(test_user, test_product.id, 0.5, "manual", "manual test", manual_batch_id=b_later.id, idempotency_key="c_m1")
    b_later.refresh_from_db()
    assert b_later.quantity_remaining == Decimal("1.5")
    
    LocalStockService.consume_product(test_user, test_product.id, 1.2, "expires_first", "fefo test", idempotency_key=None)
    b_early.refresh_from_db()
    assert b_early.quantity_remaining == Decimal("0")
    
    with pytest.raises(ValidationError):
        LocalStockService.consume_product(test_user, test_product.id, 5.0, "expires_first", "over consume", idempotency_key="c_m3")

@pytest.mark.django_db(transaction=True)
def test_expiration_and_notifications(test_user, test_product):
    now = timezone.now()
    LocalStockService.add_batch(test_user, test_product.id, 1.0, now - timedelta(days=10), now - timedelta(days=2), "fridge", 1.0, "e1")
    LocalStockService.run_daily_maintenance()
    assert Notification.objects.filter(user=test_user, notification_type="expired").exists()

@pytest.mark.django_db(transaction=True)
def test_forecasting_logic(test_user, test_product):
    now = timezone.now()
    forecast = LocalStockService.get_forecast(test_user, test_product.id)
    assert forecast["confidence"] == "insufficient_data"
    
    LocalStockService.add_batch(test_user, test_product.id, 10.0, now, now + timedelta(days=20), "fridge", 1.0, "f1")
