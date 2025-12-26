import os
from celery import Celery
from celery.schedules import crontab
import logging
from datetime import datetime, timedelta
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
# Импортируем модели
def get_models():
	from main import News, User
	return News, User

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('celery_tasks.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация Celery
celery_app = Celery(
    'news_app',
    broker='redis://localhost:6379/0',
    backend='redis://localhost:6379/0'
)

# Конфигурация Celery
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Europe/Moscow',
    enable_utc=True,
    task_routes={
        'celery_app.send_news_notification': {'queue': 'notifications'},
        'celery_app.send_weekly_digest': {'queue': 'digest'}
    },
    # Настройки ретраев
    task_default_retry_delay=30,  # 30 секунд
    task_max_retries=3,
    task_acks_late=True,
    worker_prefetch_multiplier=1
)

# Расписание для еженедельного дайджеста
celery_app.conf.beat_schedule = {
    'send-weekly-digest': {
        'task': 'celery_app.send_weekly_digest',
        'schedule': crontab(day_of_week=0, hour=9, minute=0),  # Воскресенье 9:00
    },
}

# Глобальные переменные для идемпотентности
processed_news = set()
processed_digests = set()


def get_db_session():
    """Создает новую сессию БД для Celery tasks"""
    SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://news_user:news_password@localhost/news_api")
    engine = create_engine(SQLALCHEMY_DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True
)
def send_news_notification(self, news_id: int):
    #Отправляет уведомление о новой новости всем пользователям
    try:
        # Проверка идемпотентности
        if news_id in processed_news:
            logger.info(f"Уведомление для новости {news_id} уже отправлено, пропускаем")
            return {"status": "skipped", "reason": "already_processed"}

        db = get_db_session()
        try:
            News, User = get_models()
            # Получаем новость
            news = db.query(News).filter(News.id == news_id).first()
            if not news:
                logger.error(f"Новость {news_id} не найдена")
                return {"status": "error", "reason": "news_not_found"}

            # Получаем всех пользователей
            users = db.query(User).all()

            # Логируем информацию об отправке
            notification_data = {
                "news_id": news_id,
                "news_title": news.title,
                "recipients_count": len(users),
                "sent_at": datetime.now().isoformat(),
                "recipients": [{"user_id": user.id, "email": user.email} for user in users]
            }

            logger.info(f"Отправка уведомления о новости '{news.title}'")
            logger.info(f"Получатели: {len(users)} пользователей")
            # Сохраняем в файл
            with open('notifications.log', 'a', encoding='utf-8') as f:
                f.write(
                    f"{datetime.now().isoformat()} - NEW_NOTIFICATION - {json.dumps(notification_data, ensure_ascii=False)}\n")

            # Помечаем как обработанную
            processed_news.add(news_id)

            logger.info(f" Уведомление о новости {news_id} успешно обработано")
            return {"status": "success", "recipients_count": len(users)}

        except Exception as e:
            logger.error(f"Ошибка при обработке новости {news_id}: {str(e)}")
            raise self.retry(countdown=2 ** self.request.retry * 30)
        finally:
            db.close()

    except Exception as e:
        logger.error(f"Критическая ошибка в задаче send_news_notification: {str(e)}")
        raise


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60
)
def send_weekly_digest(self):
    """
    Отправляет еженедельный дайджест новостей всем пользователям
    """
    try:
        News, User = get_models()
        current_week = datetime.now().strftime("%Y-%U")
        if current_week in processed_digests:
            logger.info(f"Дайджест за неделю {current_week} уже отправлен, пропускаем")
            return {"status": "skipped", "reason": "already_processed"}

        db = get_db_session()
        try:
            # Новости за последнюю неделю
            week_ago = datetime.now() - timedelta(days=7)
            recent_news = db.query(News).filter(
                News.publication_date >= week_ago
            ).order_by(News.publication_date.desc()).all()

            users = db.query(User).all()

            digest_data = {
                "week": current_week,
                "period": f"{week_ago.date()} - {datetime.now().date()}",
                "news_count": len(recent_news),
                "news": [
                    {
                        "id": news.id,
                        "title": news.title,
                        "publication_date": news.publication_date.isoformat(),
                        "author": news.author.name
                    } for news in recent_news
                ],
                "recipients_count": len(users),
                "sent_at": datetime.now().isoformat(),
                "recipients": [{"user_id": user.id, "email": user.email} for user in users]
            }

            logger.info(f"Отправка еженедельного дайджеста")
            logger.info(f"Новостей за неделю: {len(recent_news)}")
            logger.info(f"Получателей: {len(users)}")

            # Сохраняем в файл
            with open('digests.log', 'a', encoding='utf-8') as f:
                f.write(
                    f"{datetime.now().isoformat()} - WEEKLY_DIGEST - {json.dumps(digest_data, ensure_ascii=False)}\n")

            # Помечаем как обработанную
            processed_digests.add(current_week)

            logger.info("Еженедельный дайджест успешно отправлен")
            return {"status": "success", "news_count": len(recent_news), "recipients_count": len(users)}

        except Exception as e:
            logger.error(f"Ошибка при создании дайджеста: {str(e)}")
            raise self.retry(countdown=2 ** self.request.retry * 60)
        finally:
            db.close()

    except Exception as e:
        logger.error(f"Критическая ошибка в задаче send_weekly_digest: {str(e)}")
        raise


def celery_worker_shutdown(signum, frame):
    logger.info("Получен сигнал завершения работы Celery worker...")
    logger.info("Завершаем текущие задачи...")
    # Celery автоматически завершит текущие задачи
    exit(0)
