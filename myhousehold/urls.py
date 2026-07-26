# myproject/myhousehold/urls.py

from django.urls import path, include
from rest_framework.routers import SimpleRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import RegisterView, MeView, ProductViewSet, BatchViewSet, RecommendationView
from .views import ShoppingListViewSet, NotificationViewSet

# Регистрируем наши ViewSets для продуктов и партий товаров
router = SimpleRouter(trailing_slash=False) # Отключаем обязательный слэш в конце под требования ТЗ
router.register(r'products', ProductViewSet, basename='product')
router.register(r'batches', BatchViewSet, basename='batch')

router.register(r'shopping-list', ShoppingListViewSet, basename='shopping-list')
router.register(r'notifications', NotificationViewSet, basename='notifications')

urlpatterns = [
    # Эндпоинты авторизации из раздела 4 ТЗ
    path('auth/register', RegisterView.as_view(), name='auth_register'),
    path('auth/login', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/me', MeView.as_view(), name='auth_me'),
    path('auth/refresh', TokenRefreshView.as_view(), name='token_refresh'),

    path('recommendations', RecommendationView.as_view(), name='recommendations'),
    
    # Автоматически подключаем эндпоинты /products и /batches
    path('', include(router.urls)),
]
