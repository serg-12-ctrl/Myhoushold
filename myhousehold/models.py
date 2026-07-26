from django.db import models

# Create your models here.
from django.db import models
from django.contrib.auth.models import User

class Product(models.Model):
    class UnitChoices(models.TextChoices):
        PIECE = 'piece', 'Штука'
        GRAM = 'gram', 'Грамм'
        KILOGRAM = 'kilogram', 'Килограмм'
        MILLILITER = 'milliliter', 'Миллилитр'
        LITER = 'liter', 'Литр'
        PACKAGE = 'package', 'Упаковка'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='products')
    name = models.CharField(max_length=255, verbose_name="Название товара")
    category = models.CharField(max_length=100, blank=True, verbose_name="Категория")
    default_unit = models.CharField(
        max_length=20, 
        choices=UnitChoices.choices, 
        default=UnitChoices.PIECE,
        verbose_name="Единица измерения"
    )
    minimum_stock = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=0, 
        verbose_name="Минимальный остаток"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        indexes = [models.Index(fields=['user', 'name'])]

    def __str__(self):
        return f"{self.name} ({self.user.username})"


class Batch(models.Model):
    class StorageLocation(models.TextChoices):
        FRIDGE = 'fridge', 'Холодильник'
        FREEZER = 'freezer', 'Морозильник'
        KITCHEN_CABINET = 'kitchen_cabinet', 'Кухонный шкаф'
        PANTRY = 'pantry', 'Кладовая'
        BATHROOM = 'bathroom', 'Ванная'
        OTHER = 'other', 'Другое'

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='batches')
    quantity_initial = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Начальное количество")
    quantity_remaining = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Оставшееся количество")
    purchased_at = models.DateTimeField(verbose_name="Дата покупки")
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name="Срок годности")
    storage_location = models.CharField(
        max_length=50, 
        choices=StorageLocation.choices, 
        default=StorageLocation.OTHER,
        verbose_name="Место хранения"
    )
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Цена")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Партия товара"
        verbose_name_plural = "Партии товаров"
        ordering = ['expires_at'] # Сортировка по умолчанию для FEFO

    def __str__(self):
        return f"Партия {self.product.name} (Остаток: {self.quantity_remaining})"


class Operation(models.Model):
    class OperationType(models.TextChoices):
        PURCHASE = 'purchase', 'Покупка/Пополнение'
        CONSUME = 'consume', 'Расход'
        DISCARD = 'discard', 'Выбрасывание'
        CORRECTION = 'correction', 'Ручная корректировка'
        TRANSFER = 'transfer', 'Перемещение'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='operations')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='operations')
    batch = models.ForeignKey(Batch, on_delete=models.SET_NULL, null=True, blank=True, related_name='operations')
    operation_type = models.CharField(max_length=20, choices=OperationType.choices)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Количество")
    comment = models.TextField(blank=True, null=True, verbose_name="Комментарий")
    idempotency_key = models.CharField(max_length=255, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Операция"
        verbose_name_plural = "Операции"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.operation_type} - {self.product.name} ({self.quantity})"






class ShoppingItem(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shopping_list')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='shopping_items')
    recommended_quantity = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=255) # "low_stock", "forecast", "manual"
    priority = models.CharField(max_length=20, default='medium') # "high", "medium", "low"
    added_automatically = models.BooleanField(default=False)
    is_completed = models.BooleanField(default=False) # Флаг для отметки выполнения покупки
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Элемент списка покупок"
        verbose_name_plural = "Список покупок"


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, null=True, blank=True)
    batch = models.ForeignKey(Batch, on_delete=models.SET_NULL, null=True, blank=True)
    notification_type = models.CharField(max_length=50) # 'expiring_soon', 'expired', 'low_stock', etc.
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Уведомление"
        verbose_name_plural = "Уведомления"
        ordering = ['-created_at']

