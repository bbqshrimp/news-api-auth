import signal
from celery_app import celery_app, celery_worker_shutdown

# Регистрируем обработчик graceful shutdown
signal.signal(signal.SIGTERM, celery_worker_shutdown)
signal.signal(signal.SIGINT, celery_worker_shutdown)

if __name__ == '__main__':
    celery_app.start(['celery', 'beat', '-l', 'INFO', '-s', 'celerybeat-schedule'])