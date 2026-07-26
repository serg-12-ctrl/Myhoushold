# myproject/myhousehold/tasks.py

from celery import shared_task
from django.utils import timezone
from django.db.models import Sum, Avg
from datetime import timedelta
from .models import Batch, Operation, Product

@shared_task
def check_expired_batches():
    """Ежедневная задача: проверка сроков годности и генерация предупреждений"""
    now = timezone.now()
    warning_period = now + timedelta(days=3) # Предупреждать за 3 дня
    
    # Ищем активные партии, у которых истекает срок годности
    expiring_batches = Batch.objects.filter(
        quantity_remaining__gt=0,
        expires_at__lte=warning_period,
        expires_at__gt=now
    )
    
    for batch in expiring_batches:
        # Здесь в реальном проекте отправляется Email, Push или Telegram уведомление пользователю.
        # Для тестового задания достаточно сделать запись в лог или системную таблицу Уведомлений.
        print(f"ВНИМАНИЕ: У товара '{batch.product.name}' (партия {batch.id}) скоро истекает срок годности: {batch.expires_at}")

@shared_task
def calculate_product_forecast(product_id):
    """Задача по требованию: расчет прогнозируемой даты окончания товара на основе истории расхода за 30 дней"""
    product = Product.objects.get(id=product_id)
    now = timezone.now()
    month_ago = now - timedelta(days=30)
    
    # Считаем суммарный расход товара за последние 30 дней
    total_consumed = Operation.objects.filter(
        product=product,
        operation_type=Operation.OperationType.CONSUME,
        created_at__gte=month_ago
    ).aggregate(total=Sum('quantity'))['total'] or 0
    
    if total_consumed == 0:
        return None # Нет данных для прогноза скорости потребления
        
    # Средний расход в день
    daily_consumption_rate = total_consumed / 30
    
    # Текущий остаток товара
    current_stock = Batch.objects.filter(product=product, quantity_remaining__gt=0).aggregate(total=Sum('quantity_remaining'))['total'] or 0
    
    if current_stock == 0:
        return now.date().isoformat() # Товар уже закончился
        
    # Сколько дней осталось
    days_left = float(current_stock) / float(daily_consumption_rate)
    predicted_date = now + timedelta(days=days_left)
    
    return predicted_date.date().isoformat()
