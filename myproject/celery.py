# myproject/myproject/celery.py

import os
from celery import Celery
from celery.schedules import crontab

# Устанавливаем дефолтные настройки Django для celery
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

app = Celery('myproject')

# Читаем конфигурацию из settings.py с префиксом CELERY_
app.config_from_object('django.conf:settings', namespace='CELERY')

# Автоматически ищем задачи (tasks.py) во всех зарегистрированных приложениях (INSTALLED_APPS)
app.autodiscover_tasks()

# Настраиваем периодические задачи (Celery Beat)
app.conf.beat_schedule = {
    # Задача проверки сроков годности будет запускаться каждый день в полночь
    'check-expiration-dates-daily': {
        'task': 'myhousehold.tasks.check_expired_batches',
        'schedule': crontab(hour=0, minute=0),
    },
}
