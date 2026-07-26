from django.contrib.auth.models import User
from django.db import models
from rest_framework import generics, permissions, viewsets, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import action

from .models import Product, Batch, Operation
from .serializers import (
    RegisterSerializer, UserSerializer, ProductSerializer, 
    BatchSerializer, ConsumeSerializer, AdjustSerializer, DiscardSerializer
)
from .services import StockService

# --- АВТОРИЗАЦИЯ ---

class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    permission_classes = (permissions.AllowAny,)
    serializer_class = RegisterSerializer

class MeView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)


# --- УПРАВЛЕНИЕ ЗАПАСАМИ ---

class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_queryset(self):
        user = self.request.user
        queryset = Product.objects.filter(user=user)
        
        # Чтение параметров фильтрации (Требование 5.2)
        category = self.request.query_params.get('category')
        search = self.request.query_params.get('search')
        low_stock = self.request.query_params.get('low_stock')
        
        if category:
            queryset = queryset.filter(category=category)
        if search:
            queryset = queryset.filter(name__icontains=search)
        if low_stock == 'true':
            # Считаем остаток по активным партиям и сравниваем с лимитом
            queryset = queryset.annotate(
                total_stock=models.Sum('batches__quantity_remaining', filter=models.Q(batches__quantity_remaining__gt=0))
            ).filter(models.Q(total_stock__lt=models.F('minimum_stock')) | models.Q(total_stock__isnull=True))
            
        return queryset

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    # POST и GET /products/{product_id}/batches
    @action(detail=True, methods=['post', 'get'], url_path='batches')
    def batches(self, request, pk=None):
        product = self.get_object()
        
        if request.method == 'POST':
            batch = StockService.add_batch(
                user=request.user, product_id=product.id,
                quantity=request.data.get('quantity'),
                purchased_at=request.data.get('purchased_at'),
                expires_at=request.data.get('expires_at'),
                storage_location=request.data.get('storage_location'),
                price=request.data.get('price')
            )
            return Response(BatchSerializer(batch).data, status=status.HTTP_201_CREATED)
            
        if request.method == 'GET':
            queryset = Batch.objects.filter(product=product)
            status_filter = request.query_params.get('status')
            
            if status_filter == 'active':
                queryset = queryset.filter(quantity_remaining__gt=0)
            elif status_filter == 'consumed':
                queryset = queryset.filter(quantity_remaining=0)
                
            serializer = BatchSerializer(queryset, many=True)
            return Response(serializer.data)

    # POST /products/{product_id}/consume
    @action(detail=True, methods=['post'], url_path='consume')
    def consume(self, request, pk=None):
        serializer = ConsumeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        StockService.consume_product(
            user=request.user, product_id=pk,
            quantity=serializer.validated_data['quantity'],
            strategy=serializer.validated_data['strategy'],
            comment=serializer.validated_data.get('comment', ''),
            manual_batch_id=serializer.validated_data.get('batch_id')
        )
        return Response({"status": "success"}, status=status.HTTP_200_OK)

    # POST /products/{product_id}/adjust
    @action(detail=True, methods=['post'], url_path='adjust')
    def adjust(self, request, pk=None):
        serializer = AdjustSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        new_stock = StockService.adjust_stock(
            user=request.user, product_id=pk,
            actual_quantity=serializer.validated_data['actual_quantity'],
            comment=serializer.validated_data.get('comment', '')
        )
        return Response({"current_stock": float(new_stock)}, status=status.HTTP_200_OK)


class BatchViewSet(viewsets.GenericViewSet):
    permission_classes = (permissions.IsAuthenticated,)
    
    # POST /batches/{batch_id}/discard
    @action(detail=True, methods=['post'], url_path='discard')
    def discard(self, request, pk=None):
        serializer = DiscardSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        StockService.discard_batch(
            user=request.user, batch_id=pk,
            quantity=serializer.validated_data['quantity'],
            reason=serializer.validated_data['reason']
        )
        return Response({"status": "success"}, status=status.HTTP_200_OK)
