# myproject/myhousehold/services.py

from decimal import Decimal  # <-- 1. Важный импорт для точных складских расчетов
from django.db import transaction, models
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import Product, Batch, Operation, ShoppingItem, Notification

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
    

    @staticmethod
    def get_forecast(user, product_id, days_to_analyze=14):
        """Реализация Требования 7: Прогнозирование расхода без ML"""
        product = Product.objects.get(id=product_id, user=user)
        now = timezone.now()
        start_date = now - timedelta(days=days_to_analyze)

        # Текущий остаток
        current_stock = float(Batch.objects.filter(product=product, quantity_remaining__gt=0)
                              .aggregate(total=Sum('quantity_remaining'))['total'] or 0)

        # Берем только операции расхода за период
        consume_ops = Operation.objects.filter(
            product=product,
            operation_type=Operation.OperationType.CONSUME,
            created_at__gte=start_date
        ).order_by('created_at')

        # Если операций списания критически мало (меньше 2), данных для прогноза недостаточно
        if consume_ops.count() < 2:
            return {
                "product_id": product.id, "current_stock": current_stock, "average_daily_consumption": 0.0,
                "estimated_days_remaining": None, "estimated_depletion_date": None,
                "confidence": "insufficient_data", "based_on_days": days_to_analyze
            }

        quantities = [float(op.quantity) for op in consume_ops]
        
        # Эвристика отсечения резких аномальных списаний (например, случайно списали в 4 раза больше обычного)
        # Рассчитаем медиану и отсечем операции, превышающие 3 медианных значения
        quantities.sort()
        median_quantity = quantities[len(quantities) // 2]
        filtered_ops = consume_ops.filter(quantity__lte=median_quantity * 4)

        total_consumed = float(filtered_ops.aggregate(total=Sum('quantity'))['total'] or 0)
        
        # Расчет среднего дневного расхода с учетом дней БЕЗ расхода
        average_daily_consumption = total_consumed / days_to_analyze

        if average_daily_consumption == 0:
            return {
                "product_id": product.id, "current_stock": current_stock, "average_daily_consumption": 0.0,
                "estimated_days_remaining": None, "estimated_depletion_date": None,
                "confidence": "insufficient_data", "based_on_days": days_to_analyze
            }

        # Вычисляем оставшиеся дни
        estimated_days_remaining = int(current_stock // average_daily_consumption)
        estimated_depletion_date = (now + timedelta(days=estimated_days_remaining)).date()

        # Оценка уверенности (Confidence Score)
        # Если операций много (> 5) — высокая, если мало — средняя
        confidence = "high" if filtered_ops.count() >= 6 else "medium"

        return {
            "product_id": product.id,
            "current_stock": current_stock,
            "average_daily_consumption": round(average_daily_consumption, 2),
            "estimated_days_remaining": estimated_days_remaining,
            "estimated_depletion_date": estimated_depletion_date,
            "confidence": confidence,
            "based_on_days": days_to_analyze
        }
    

    @staticmethod
    def generate_recommendations(user):
        """Реализация Требования 8: Формирование четырех видов рекомендаций"""
        recommendations = []
        now = timezone.now()
        
        # Заданный пользователем порог (по умолчанию 3 дня) под Требование 6
        expiring_days_threshold = 3 
        warning_period = now + timedelta(days=expiring_days_threshold)

        products = Product.objects.filter(user=user)

        for product in products:
            # Считаем остаток
            batches = Batch.objects.filter(product=product, quantity_remaining__gt=0)
            current_stock = float(batches.aggregate(total=Sum('quantity_remaining'))['total'] or 0)
            
            # Получаем прогноз расхода для анализа
            forecast = StockService.get_forecast(user, product.id)
            daily_rate = forecast["average_daily_consumption"]

            # --- 8.1 РЕКОМЕНДАЦИЯ: Использовать в первую очередь (use_soon) ---
            expiring_batches = batches.filter(expires_at__lte=warning_period, expires_at__gt=now)
            for batch in expiring_batches:
                days_left = (batch.expires_at - now).days
                recommendations.append({
                    "type": "use_soon", "priority": "high", "product_id": product.id, "batch_id": batch.id,
                    "message": f"Используйте {product.name} (партия {batch.id}) в течение {days_left if days_left > 0 else 1} дн.",
                    "expires_at": batch.expires_at
                })

            # --- 8.2 РЕКОМЕНДАЦИЯ: Купить (buy) ---
            if current_stock == 0:
                recommendations.append({
                    "type": "buy", "priority": "high", "product_id": product.id,
                    "message": f"Товар {product.name} полностью закончился!", "recommended_quantity": float(product.minimum_stock or 1)
                })
            elif current_stock < float(product.minimum_stock):
                recommendations.append({
                    "type": "buy", "priority": "medium", "product_id": product.id,
                    "message": f"Остаток {product.name} ниже минимального лимита.",
                    "recommended_quantity": float(product.minimum_stock) - current_stock
                })
            elif forecast["estimated_days_remaining"] is not None and forecast["estimated_days_remaining"] <= 3:
                # Если по прогнозу закончится быстрее, чем за 3 дня
                recommendations.append({
                    "type": "buy", "priority": "medium", "product_id": product.id,
                    "message": f"{product.name} закончится примерно через {forecast['estimated_days_remaining']} дн.",
                    "recommended_quantity": float(product.minimum_stock or 1)
                })

            # --- 8.4 РЕКОМЕНДАЦИЯ: Риск потери (waste_risk) ---
            if daily_rate > 0:
                for batch in batches.filter(expires_at__isnull=False, expires_at__gt=now):
                    days_until_expiration = (batch.expires_at - now).days
                    # Сколько успеем потребить из этой партии до просрочки
                    expected_consumption = daily_rate * days_until_expiration
                    rem = float(batch.quantity_remaining)
                    
                    if rem > expected_consumption:
                        unused = rem - expected_consumption
                        recommendations.append({
                            "type": "waste_risk", "priority": "high", "product_id": product.id, "batch_id": batch.id,
                            "message": f"Вы можете не успеть использовать {round(unused, 2)} ед. товара {product.name} до конца срока годности!",
                            "expected_unused_quantity": round(unused, 2)
                        })

            # --- 8.3 РЕКОМЕНДАЦИЯ: Проверить запас (check_stock) ---
            last_op = Operation.objects.filter(product=product).order_by('-created_at').first()
            if last_op and (now - last_op.created_at).days >= 30:
                recommendations.append({
                    "type": "check_stock", "priority": "low", "product_id": product.id,
                    "message": f"По товару {product.name} не было операций более 30 дней. Подтвердите актуальный остаток."
                })

        return recommendations
    @staticmethod
    @transaction.atomic
    def add_manual_shopping_item(user, product_id, quantity, priority='medium'):
        """Реализация 9.2: Ручное добавление в список покупок"""
        product = Product.objects.get(id=product_id, user=user)
        return ShoppingItem.objects.create(
            user=user, product=product, recommended_quantity=Decimal(str(quantity)),
            reason="manual", priority=priority, added_automatically=False
        )

    @staticmethod
    @transaction.atomic
    def complete_purchase(user, item_id, batch_data=None):
        """Реализация 9.4: Завершение покупки с опциональным созданием партии"""
        item = ShoppingItem.objects.select_for_update().get(id=item_id, user=user)
        
        if batch_data:
            # Создаем новую партию товара по результатам покупки
            StockService.add_batch(
                user=user, product_id=item.product.id,
                quantity=batch_data.get('quantity'),
                purchased_at=batch_data.get('purchased_at'),
                expires_at=batch_data.get('expires_at'),
                storage_location=batch_data.get('storage_location'),
                price=batch_data.get('price')
            )
            
        # Отмечаем элемент выполненным (или удаляем)
        item.is_completed = True
        item.save()
        return item

    @staticmethod
    @transaction.atomic
    def run_daily_maintenance():
        """Реализация 10: Ежедневная фоновая задача (Идемпотентная)"""
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Обходим всех активных пользователей системы
        for user in User.objects.all():
            products = Product.objects.filter(user=user)
            
            for product in products:
                batches = Batch.objects.filter(product=product, quantity_remaining__gt=0)
                current_stock = float(batches.aggregate(total=Sum('quantity_remaining'))['total'] or 0)
                forecast = StockService.get_forecast(user, product.id)
                daily_rate = forecast["average_daily_consumption"]

                # 1. Проверка просрочки и скорого истечения (Порог из ТЗ: 3 дня)
                for batch in batches:
                    if batch.expires_at:
                        days_left = (batch.expires_at - now).days
                        
                        if batch.expires_at < now:
                            # Товар просрочен. Защита от дублей: проверяем, создавали ли уже ТАКОЕ уведомление СЕГОДНЯ
                            if not Notification.objects.filter(user=user, batch=batch, notification_type='expired', created_at__gte=today_start).exists():
                                Notification.objects.create(
                                    user=user, product=product, batch=batch, notification_type='expired',
                                    message=f"Товар {product.name} (партия {batch.id}) просрочен!"
                                )
                        elif 0 <= days_left <= 3:
                            # Истекает скоро
                            if not Notification.objects.filter(user=user, batch=batch, notification_type='expiring_soon', created_at__gte=today_start).exists():
                                Notification.objects.create(
                                    user=user, product=product, batch=batch, notification_type='expiring_soon',
                                    message=f"Срок годности {product.name} (партия {batch.id}) истекает через {days_left} дн."
                                )

                # 2. Низкий остаток -> Авто-обновление Списка покупок и Уведомление
                if current_stock == 0 or current_stock < float(product.minimum_stock):
                    # Отправляем уведомление
                    if not Notification.objects.filter(user=user, product=product, notification_type='low_stock', created_at__gte=today_start).exists():
                        Notification.objects.create(
                            user=user, product=product, notification_type='low_stock',
                            message=f"Запасы товара {product.name} на исходе или закончились."
                        )
                    # Идемпотентно добавляем в список покупок: если активный элемент уже есть — не дублируем
                    if not ShoppingItem.objects.filter(user=user, product=product, is_completed=False).exists():
                        rec_qty = float(product.minimum_stock) - current_stock if current_stock < float(product.minimum_stock) else float(product.minimum_stock or 1)
                        ShoppingItem.objects.create(
                            user=user, product=product, recommended_quantity=Decimal(str(rec_qty)),
                            reason="low_stock", priority="high", added_automatically=True
                        )

                # 3. Риск потери (Выбрасывания)
                if daily_rate > 0:
                    for batch in batches.filter(expires_at__isnull=False, expires_at__gt=now):
                        days_until_expiration = (batch.expires_at - now).days
                        expected_consumption = daily_rate * days_until_expiration
                        rem = float(batch.quantity_remaining)
                        
                        if rem > expected_consumption:
                            if not Notification.objects.filter(user=user, batch=batch, notification_type='waste_risk', created_at__gte=today_start).exists():
                                Notification.objects.create(
                                    user=user, product=product, batch=batch, notification_type='waste_risk',
                                    message=f"Высокий риск выбросить часть товара {product.name} до конца срока годности!"
                                )