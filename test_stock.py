# myproject/myhousehold/test_stock.py

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from rest_framework.exceptions import ValidationError
from myhousehold.models import Product, Batch
from myhousehold.services import StockService

@pytest.fixture
def test_user(db):
    """Фикстура для создания тестового пользователя"""
    return User.objects.create_user(username="chef", password="password123")

@pytest.fixture
def test_product(test_user):
    """Фикстура для создания товара"""
    return Product.objects.create(
        user=test_user,
        name="Молоко",
        category="dairy",
        default_unit="liter",
        minimum_stock=2
    )

def test_fefo_consecutive_consumption(test_user, test_product):
    """Тест стратегии FEFO: Списание должно каскадно затрагивать несколько партий"""
    now = timezone.now()
    
    # Партия 1: Истекает через 2 дня (0.5 литра) -> должна уйти первой
    batch_early = StockService.add_batch(
        user=test_user, product_id=test_product.id, quantity=0.5,
        purchased_at=now, expires_at=now + timedelta(days=2),
        storage_location="fridge", price=1.0
    )
    
    # Партия 2: Истекает через 5 дней (2.0 литра) -> должна закрыть остаток
    batch_later = StockService.add_batch(
        user=test_user, product_id=test_product.id, quantity=2.0,
        purchased_at=now, expires_at=now + timedelta(days=5),
        storage_location="fridge", price=1.0
    )

    # Запрашиваем списание 1.5 литра молока
    StockService.consume_product(
        user=test_user, product_id=test_product.id, 
        quantity=1.5, strategy="expires_first", comment="Тест"
    )

    # Проверяем остатки в базах данных
    batch_early.refresh_from_db()
    batch_later.refresh_from_db()

    assert batch_early.quantity_remaining == 0  # Первая партия полностью израсходована
    assert batch_later.quantity_remaining == 1.0 # Из второй списали оставшийся 1 литр

def test_insufficient_stock_raises_error(test_user, test_product):
    """Тест: Система должна выдать ошибку, если запрашивается больше, чем есть"""
    now = timezone.now()
    
    StockService.add_batch(
        user=test_user, product_id=test_product.id, quantity=1.0,
        purchased_at=now, expires_at=now + timedelta(days=5),
        storage_location="fridge", price=1.0
    )

    # Пытаемся списать 2 литра, когда есть только 1
    with pytest.raises(ValidationError) as exc_info:
        StockService.consume_product(
            user=test_user, product_id=test_product.id, 
            quantity=2.0, strategy="expires_first", comment="Тест"
        )
    
    # Проверяем, что вернулся кастомный код ошибки из ТЗ
    assert "INSUFFICIENT_STOCK" in str(exc_info.value)
