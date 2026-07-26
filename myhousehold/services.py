# myproject/myhousehold/services.py

from decimal import Decimal
from datetime import timedelta
from django.db import transaction, models
from django.db.models import Sum, F
from django.utils import timezone
from rest_framework import status

from .models import Product, Batch, Operation, ShoppingItem, Notification
from .exceptions import CustomBusinessException

class StockService:
    
    @staticmethod
    def check_idempotency(user, key):
        """Реализация 13: Проверка ключа идемпотентности"""
        if key and Operation.objects.filter(user=user, idempotency_key=key).exists():
            raise CustomBusinessException(
                code="DUPLICATE_IDEMPOTENCY_KEY",
                message="Операция с данным ключом идемпотентности уже была выполнена ранее.",
                status_code=status.HTTP_409_CONFLICT
            )

    @staticmethod
    @transaction.atomic
    def add_batch(user, product_id, quantity, purchased_at, expires_at, storage_location, price, idempotency_key=None):
        """Реализация 5.3, 13, 15: Добавление партии с валидацией и идемпотентностью"""
        StockService.check_idempotency(user, idempotency_key)

        try:
            product = Product.objects.select_for_update().get(id=product_id, user=user)
        except Product.DoesNotExist:
            raise CustomBusinessException("PRODUCT_NOT_FOUND", "Товар не найден.", status.HTTP_404_NOT_FOUND)
        
        quantity = Decimal(str(quantity))
        if quantity <= 0:
            raise CustomBusinessException("VALIDATION_ERROR", "Количество должно быть больше нуля.")
        
        if price is not None:
            price = Decimal(str(price))
            if price < 0:
                raise CustomBusinessException("VALIDATION_ERROR", "Цена не может быть отрицательной.")
        
        if expires_at and purchased_at and expires_at < purchased_at:
            raise CustomBusinessException("INVALID_EXPIRATION_DATE", "Срок годности не может быть раньше даты покупки.")
            
        batch = Batch.objects.create(
            product=product,
            quantity_initial=quantity,
            quantity_remaining=quantity,
            purchased_at=purchased_at,
            expires_at=expires_at,
            storage_location=storage_location,
            price=price
        )
        
        Operation.objects.create(
            user=user, product=product, batch=batch,
            operation_type=Operation.OperationType.PURCHASE,
            quantity=quantity, idempotency_key=idempotency_key,
            comment="Пополнение запасов"
        )
        return batch

    @staticmethod
    @transaction.atomic
    def consume_product(user, product_id, quantity, strategy, comment, manual_batch_id=None, idempotency_key=None):
        """Реализация 5.5, 13, 14, 15: Списание по стратегиям с пессимистической блокировкой строк"""
        StockService.check_idempotency(user, idempotency_key)

        try:
            product = Product.objects.get(id=product_id, user=user)
        except Product.DoesNotExist:
            raise CustomBusinessException("PRODUCT_NOT_FOUND", "Товар не найден.", status.HTTP_404_NOT_FOUND)
        
        quantity = Decimal(str(quantity))
        if quantity <= 0:
            raise CustomBusinessException("VALIDATION_ERROR", "Количество для списания должно быть больше нуля.")
        
        batches_query = Batch.objects.select_for_update().filter(product=product, quantity_remaining__gt=0)
        
        if strategy == 'expires_first':
            batches_query = batches_query.order_by(models.F('expires_at').asc(nulls_last=True))
        elif strategy == 'oldest_first':
            batches_query = batches_query.order_by('purchased_at')
        elif strategy == 'manual':
            if not manual_batch_id:
                raise CustomBusinessException("VALIDATION_ERROR", "При ручной стратегии необходимо передать batch_id.")
            batches_query = batches_query.filter(id=manual_batch_id)
        else:
            raise CustomBusinessException("VALIDATION_ERROR", "Неподдерживаемая стратегия списания.")

        batches = list(batches_query)
        available = sum(b.quantity_remaining for b in batches)
        
        if available < quantity:
            raise CustomBusinessException(
                code="INSUFFICIENT_STOCK",
                message="Недостаточно товара на складе.",
                status_code=status.HTTP_400_BAD_REQUEST,
                details={"requested": float(quantity), "available": float(available)}
            )

        remaining_to_consume = quantity
        
        for batch in batches:
            if remaining_to_consume <= 0:
                break
            
            # Раздел 14: Уникальный суффикс ключа для каскадных логов списания из нескольких партий
            batch_idem_key = f"{idempotency_key}_b{batch.id}" if idempotency_key else None
                
            if batch.quantity_remaining >= remaining_to_consume:
                batch.quantity_remaining -= remaining_to_consume
                batch.save()
                
                Operation.objects.create(
                    user=user, product=product, batch=batch,
                    operation_type=Operation.OperationType.CONSUME,
                    quantity=remaining_to_consume, comment=comment,
                    idempotency_key=batch_idem_key
                )
                remaining_to_consume = 0
            else:
                consumed_from_batch = batch.quantity_remaining
                remaining_to_consume -= consumed_from_batch
                batch.quantity_remaining = 0
                batch.save()
                
                Operation.objects.create(
                    user=user, product=product, batch=batch,
                    operation_type=Operation.OperationType.CONSUME,
                    quantity=consumed_from_batch, comment=comment,
                    idempotency_key=batch_idem_key
                )
                
        return True

    @staticmethod
    @transaction.atomic
    def discard_batch(user, batch_id, quantity, reason, idempotency_key=None):
        """Реализация 5.6, 13, 15: Утилизация товара из конкретной партии"""
        StockService.check_idempotency(user, idempotency_key)
        
        quantity = Decimal(str(quantity))
        if quantity <= 0:
            raise CustomBusinessException("VALIDATION_ERROR", "Количество должно быть больше нуля.")

        try:
            batch = Batch.objects.select_for_update().get(id=batch_id, product__user=user)
        except Batch.DoesNotExist:
            raise CustomBusinessException("BATCH_NOT_FOUND", "Партия товара не найдена.", status.HTTP_404_NOT_FOUND)
        
        if batch.quantity_remaining < quantity:
            raise CustomBusinessException(
                code="VALIDATION_ERROR",
                message=f"Нельзя выбросить {quantity}, в партии осталось только {batch.quantity_remaining}"
            )
            
        batch.quantity_remaining -= quantity
        batch.save()
        
        Operation.objects.create(
            user=user, product=batch.product, batch=batch,
            operation_type=Operation.OperationType.DISCARD,
            quantity=quantity, idempotency_key=idempotency_key,
            comment=f"Выбрасывание. Причина: {reason}"
        )
        return batch

    @staticmethod
    @transaction.atomic
    def adjust_stock(user, product_id, actual_quantity, comment, idempotency_key=None):
        """Реализация 5.7, 13, 15: Корректировка остатка"""
        StockService.check_idempotency(user, idempotency_key)
        
        actual_quantity = Decimal(str(actual_quantity))
        if actual_quantity < 0:
            raise CustomBusinessException("VALIDATION_ERROR", "Фактическое количество не может быть отрицательным.")

        try:
            product = Product.objects.get(id=product_id, user=user)
        except Product.DoesNotExist:
            raise CustomBusinessException("PRODUCT_NOT_FOUND", "Товар не найден.", status.HTTP_404_NOT_FOUND)

        # ДОПИСЫВАЕМ СЛОМАННЫЙ МЕТОД
        batches = list(Batch.objects.select_for_update().filter(product=product).order_by(models.F('expires_at').asc(nulls_last=True)))
        current_stock = sum(b.quantity_remaining for b in batches)
        diff = actual_quantity - current_stock
        
        if diff == 0:
            return current_stock
            
        if diff > 0:
            batch = Batch.objects.create(
                product=product, quantity_initial=diff, quantity_remaining=diff,
                purchased_at=timezone.now(), storage_location=Batch.StorageLocation.OTHER
            )
            Operation.objects.create(
                user=user, product=product, batch=batch,
                operation_type=Operation.OperationType.CORRECTION,
                quantity=diff, comment=comment, idempotency_key=idempotency_key
            )
        else:
            remaining_to_remove = abs(diff)
            for batch in batches:
                if remaining_to_remove <= 0:
                    break
                
                batch_idem_key = f"{idempotency_key}_adj{batch.id}" if idempotency_key else None
                
                if batch.quantity_remaining >= remaining_to_remove:
                    batch.quantity_remaining -= remaining_to_remove
                    batch.save()
                    Operation.objects.create(
                        user=user, product=product, batch=batch,
                        operation_type=Operation.OperationType.CORRECTION,
                        quantity=remaining_to_remove, comment=comment, idempotency_key=batch_idem_key
                    )
                    remaining_to_remove = 0
                else:
                    removed = batch.quantity_remaining
                    remaining_to_remove -= removed
                    batch.quantity_remaining = 0
                    batch.save()
