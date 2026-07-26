# myproject/myhousehold/tasks.py

from celery import shared_task
from django.utils import timezone
from django.db.models import Sum, Avg
from datetime import timedelta
from .models import Batch, Operation, Product

@shared_task
def check_expired_batches():
    """Ежедневный запуск аналитики, идемпотентного пересчета прогнозов и генерации списков покупок"""
    from .services import StockService
    StockService.run_daily_maintenance()
    return "Daily maintenance successfully completed."

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
