# myproject/myhousehold/services.py

from decimal import Decimal  # <-- 1. Важный импорт для точных складских расчетов
from django.db import transaction, models
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import Product, Batch, Operation

class StockService:
    
    @staticmethod
    @transaction.atomic
    def add_batch(user, product_id, quantity, purchased_at, expires_at, storage_location, price):
        """Реализация требования 5.3: Добавление партии"""
        product = Product.objects.select_for_update().get(id=product_id, user=user)
        
        # Приводим к Decimal, если передали float/str
        quantity = Decimal(str(quantity))
        if price is not None:
            price = Decimal(str(price))
        
        if expires_at and purchased_at and expires_at < purchased_at:
            raise ValidationError({"expires_at": "Дата окончания срока годности не может быть раньше даты покупки."})
            
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
            user=user,
            product=product,
            batch=batch,
            operation_type=Operation.OperationType.PURCHASE,
            quantity=quantity,
            comment="Пополнение запасов"
        )
        return batch

    @staticmethod
    @transaction.atomic
    def consume_product(user, product_id, quantity, strategy, comment, manual_batch_id=None):
        """Реализация требования 5.5: Списание товара с защитой от Race Conditions"""
        product = Product.objects.get(id=product_id, user=user)
        
        # 2. Переводим входящее количество в Decimal, чтобы не было конфликтов типов с БД
        quantity = Decimal(str(quantity))
        
        # Блокируем строки активных партий этого товара для текущего пользователя в БД
        batches_query = Batch.objects.select_for_update().filter(product=product, quantity_remaining__gt=0)
        
        # Применяем стратегию сортировки
        if strategy == 'expires_first':
            batches_query = batches_query.order_by(models.F('expires_at').asc(nulls_last=True))
        elif strategy == 'oldest_first':
            batches_query = batches_query.order_by('purchased_at')
        elif strategy == 'manual':
            if not manual_batch_id:
                raise ValidationError({"batch_id": "При ручной стратегии необходимо передать batch_id"})
            batches_query = batches_query.filter(id=manual_batch_id)
        else:
            raise ValidationError({"strategy": "Неподдерживаемая стратегия списания"})

        batches = list(batches_query)
        
        # Считаем доступный остаток среди заблокированных партий
        available = sum(b.quantity_remaining for b in batches)
        
        if available < quantity:
            raise ValidationError({
                "code": "INSUFFICIENT_STOCK",
                "message": "Недостаточно товара",
                "details": {
                    "requested": float(quantity),
                    "available": float(available)
                }
            })

        remaining_to_consume = quantity
        
        for batch in batches:
            if remaining_to_consume <= 0:
                break
                
            if batch.quantity_remaining >= remaining_to_consume:
                batch.quantity_remaining -= remaining_to_consume
                batch.save()
                
                Operation.objects.create(
                    user=user, product=product, batch=batch,
                    operation_type=Operation.OperationType.CONSUME,
                    quantity=remaining_to_consume, comment=comment
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
                    quantity=consumed_from_batch, comment=comment
                )
                
        return True

    @staticmethod
    @transaction.atomic
    def discard_batch(user, batch_id, quantity, reason):
        """Реализация требования 5.6: Выбрасывание товара"""
        quantity = Decimal(str(quantity))
        batch = Batch.objects.select_for_update().get(id=batch_id, product__user=user)
        
        if batch.quantity_remaining < quantity:
            raise ValidationError(f"Нельзя выбросить {quantity}, в партии осталось только {batch.quantity_remaining}")
            
        batch.quantity_remaining -= quantity
        batch.save()
        
        Operation.objects.create(
            user=user,
            product=batch.product,
            batch=batch,
            operation_type=Operation.OperationType.DISCARD,
            quantity=quantity,
            comment=f"Выбрасывание. Причина: {reason}"
        )
        return batch

    @staticmethod
    @transaction.atomic
    def adjust_stock(user, product_id, actual_quantity, comment):
        """Реализация требования 5.7: Корректировка остатка"""
        actual_quantity = Decimal(str(actual_quantity))
        product = Product.objects.get(id=product_id, user=user)
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
                quantity=diff, comment=comment
            )
        else:
            remaining_to_remove = abs(diff)
            for batch in batches:
                if remaining_to_remove <= 0:
                    break
                if batch.quantity_remaining >= remaining_to_remove:
                    batch.quantity_remaining -= remaining_to_remove
                    batch.save()
                    Operation.objects.create(
                        user=user, product=product, batch=batch,
                        operation_type=Operation.OperationType.CORRECTION,
                        quantity=remaining_to_remove, comment=comment
                    )
                    remaining_to_remove = 0
                else:
                    removed = batch.quantity_remaining
                    remaining_to_remove -= removed
                    batch.quantity_remaining = 0
                    batch.save()
                    Operation.objects.create(
                        user=user, product=product, batch=batch,
                        operation_type=Operation.OperationType.CORRECTION,
                        quantity=removed, comment=comment
                    )
        return actual_quantity
