
from celery_app import send_news_notification
from celery_app import celery_app
from fastapi import FastAPI, Depends, HTTPException, Request, status
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from sqlalchemy.sql import func
from pydantic import BaseModel
from typing import Optional, List
import datetime
import os
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import uuid
from dotenv import load_dotenv
from fastapi_sso.sso.github import GithubSSO
import redis
import json

load_dotenv()

# Redis подключение
redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

# Database - PostgreSQL
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://news_user:news_password@localhost/news_api")
engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# GitHub OAuth
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")

github_sso = GithubSSO(
    client_id=GITHUB_CLIENT_ID,
    client_secret=GITHUB_CLIENT_SECRET,
    redirect_uri="http://localhost:8000/github/callback"
)

# JWT настройки
SECRET_KEY = os.getenv("SECRET_KEY", "fallback-secret-key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7

security = HTTPBearer()
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


# Хэширование паролей
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


# JWT функции
def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access", "sub": str(data["sub"])})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token():
    return str(uuid.uuid4())


# Redis функции
def cache_news(news_id: int, news_data: dict):
    """Кэшируем новость на 5 минут"""
    redis_client.setex(f"news:{news_id}", 300, json.dumps(news_data))


def get_cached_news(news_id: int):
    """Получаем новость из кэша"""
    cached = redis_client.get(f"news:{news_id}")
    if cached:
        print("Новость из кэша")
        return json.loads(cached)
    print("Новость из БД")
    return None


def cache_user(user_id: int, user_data: dict):
    """Кэшируем пользователя (без пароля) на 10 минут"""
    safe_user_data = {k: v for k, v in user_data.items() if k != 'hashed_password'}
    redis_client.setex(f"user:{user_id}", 600, json.dumps(safe_user_data))


def get_cached_user(user_id: int):
    """Получаем пользователя из кэша"""
    cached = redis_client.get(f"user:{user_id}")
    if cached:
        print("Пользователь из кэша")
        return json.loads(cached)
    print("Пользователь из БД")
    return None


def cache_session(refresh_token: str, user_id: int):
    """Кэшируем сессию на 7 дней"""
    session_data = {"user_id": user_id, "created_at": datetime.datetime.utcnow().isoformat()}
    redis_client.setex(f"session:{refresh_token}", 604800, json.dumps(session_data))  # 7 дней
    print("Сессия сохранена в кэш")


def get_cached_session(refresh_token: str):
    """Получаем сессию из кэша"""
    cached = redis_client.get(f"session:{refresh_token}")
    if cached:
        print("Сессия из кэша")
        return json.loads(cached)
    print("Сессия не найдена в кэше")
    return None


# Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=True)  # Может быть null для OAuth пользователей
    registration_date = Column(DateTime, default=datetime.datetime.utcnow)
    is_verified_author = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)
    avatar = Column(String, nullable=True)
    github_id = Column(String, nullable=True)

    news = relationship("News", back_populates="author")
    comments = relationship("Comment", back_populates="author")


class News(Base):
    __tablename__ = "news"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    publication_date = Column(DateTime, default=datetime.datetime.utcnow)
    author_id = Column(Integer, ForeignKey("users.id"))
    cover_image = Column(String, nullable=True)
    author = relationship("User", back_populates="news")
    comments = relationship("Comment", back_populates="news", cascade="all, delete")


class Comment(Base):
    __tablename__ = "comments"
    id = Column(Integer, primary_key=True, index=True)
    text = Column(Text, nullable=False)
    publication_date = Column(DateTime, default=datetime.datetime.utcnow)
    news_id = Column(Integer, ForeignKey("news.id"))
    author_id = Column(Integer, ForeignKey("users.id"))
    news = relationship("News", back_populates="comments")
    author = relationship("User", back_populates="comments")


'''
class RefreshSession(Base):
    __tablename__ = "refresh_sessions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    refresh_token = Column(String, unique=True, index=True)
    user_agent = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    expires_at = Column(DateTime)
    user = relationship("User")
'''

# Create tables
Base.metadata.create_all(bind=engine)


# Зависимости аутентификации
async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security),
                           db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("sub")
        user_id: int = int(user_id_str)

        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")

        # Сначала проверяем кэш
        cached_user = get_cached_user(user_id)
        if cached_user:
            print("Пользователь из кэша")
            # ВСЕГДА возвращаем объект User из БД для consistency
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                return user

        # Если нет в кэше - ищем в БД
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")

        # Сохраняем в кэш
        user_dict = {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "is_verified_author": user.is_verified_author,
            "is_admin": user.is_admin
        }
        cache_user(user_id, user_dict)
        print("Пользователь из БД и сохранен в кэш")

        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


# Резолвер для проверки прав на новость
async def get_news_with_permission(
        news_id: int,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    news = db.query(News).filter(News.id == news_id).first()
    if not news:
        raise HTTPException(status_code=404, detail="News not found")

    # Админ может всё
    if user.is_admin:
        return news

    # Автор может управлять своими новостями
    if news.author_id == user.id:
        return news

    raise HTTPException(status_code=403, detail="Not authorized to modify this news")


# Резолвер для проверки прав на комментарий
async def get_comment_with_permission(
        comment_id: int,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if user.is_admin or comment.author_id == user.id:
        return comment

    raise HTTPException(status_code=403, detail="Not authorized to modify this comment")


# Зависимости авторизации
def require_author(db: Session = Depends(get_db), user=Depends(get_current_user)):
    # Если user - словарь (из кэша), берем ID из него
    if isinstance(user, dict):
        user_id = user["id"]
    else:
        user_id = user.id

    # Обновляем данные пользователя из базы
    fresh_user = db.query(User).filter(User.id == user_id).first()
    if not fresh_user.is_verified_author and not fresh_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized to create news")
    return fresh_user


def require_admin(user: User = Depends(get_current_user)):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# Schemas
class UserBase(BaseModel):
    name: str
    email: str
    is_verified_author: bool = False
    avatar: Optional[str] = None


class UserCreate(BaseModel):
    name: str
    email: str
    password: str


class UserLogin(BaseModel):
    email: str
    password: str


class UserResponse(UserBase):
    id: int
    registration_date: datetime.datetime

    class Config:
        from_attributes = True


class NewsBase(BaseModel):
    title: str
    content: str
    cover_image: Optional[str] = None


class NewsCreate(NewsBase):
    pass


class NewsResponse(NewsBase):
    id: int
    publication_date: datetime.datetime
    author_id: int

    class Config:
        from_attributes = True


class CommentBase(BaseModel):
    text: str


class CommentCreate(CommentBase):
    news_id: int


class CommentResponse(CommentBase):
    id: int
    publication_date: datetime.datetime
    news_id: int
    author_id: int

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str


class RefreshRequest(BaseModel):
    refresh_token: str


# FastAPI app
app = FastAPI()


# GitHub OAuth endpoints
@app.get("/github/login")
async def github_login():
    """Редирект на GitHub OAuth"""
    if not GITHUB_CLIENT_ID or GITHUB_CLIENT_SECRET == "test-client-secret":
        raise HTTPException(
            status_code=501,
            detail="GitHub OAuth не настроен. Используйте тестовые credentials."
        )
    return await github_sso.get_login_redirect()


@app.get("/github/callback")
async def github_callback(request: Request, db: Session = Depends(get_db)):
    """Обработка callback от GitHub OAuth"""
    if not GITHUB_CLIENT_ID or GITHUB_CLIENT_SECRET == "test-client-secret":
        raise HTTPException(
            status_code=501,
            detail="GitHub OAuth не настроен"
        )
    try:
        user_info = await github_sso.verify_and_process(request)

        if not user_info:
            raise HTTPException(status_code=400, detail="Failed to authenticate with GitHub")

        # Ищем пользователя по GitHub ID или email
        user = db.query(User).filter(
            (User.github_id == user_info.id) | (User.email == user_info.email)
        ).first()

        if not user:
            # Создаем нового пользователя
            user = User(
                name=user_info.display_name or user_info.email,
                email=user_info.email,
                github_id=user_info.id,
                avatar=user_info.picture,
                is_verified_author=False,  # По умолчанию не верифицирован
                is_admin=False
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        # Создаем токены
        access_token = create_access_token(data={"sub": user.id})
        refresh_token = create_refresh_token()

        # Сохраняем сессию
        user_agent = request.headers.get("user-agent", "unknown")
        session = RefreshSession(
            user_id=user.id,
            refresh_token=refresh_token,
            user_agent=user_agent,
            expires_at=datetime.datetime.utcnow() + datetime.timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        )
        db.add(session)
        db.commit()

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "is_verified_author": user.is_verified_author,
                "is_admin": user.is_admin
            }
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OAuth error: {str(e)}")


'''
@app.get("/my-sessions")
def get_my_sessions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Получить мои активные сессии"""
    sessions = db.query(RefreshSession).filter(RefreshSession.user_id == user.id).all()
    return sessions
'''


@app.get("/me")
def get_current_user_info(user: User = Depends(get_current_user)):
    """Информация о текущем пользователе"""
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "is_verified_author": user.is_verified_author,
        "is_admin": user.is_admin
    }


@app.get("/news/{news_id}", response_model=NewsResponse)
def read_news(news_id: int, db: Session = Depends(get_db)):
    # Сначала проверяем кэш
    cached_news = get_cached_news(news_id)
    if cached_news:
        # Конвертируем строку даты обратно в datetime
        cached_news["publication_date"] = datetime.datetime.fromisoformat(cached_news["publication_date"])
        return NewsResponse(**cached_news)

    # Если нет в кэше - ищем в БД
    news = db.query(News).filter(News.id == news_id).first()
    if not news:
        raise HTTPException(status_code=404, detail="News not found")

    # Сохраняем в кэш
    news_dict = {
        "id": news.id,
        "title": news.title,
        "content": news.content,
        "cover_image": news.cover_image,
        "publication_date": news.publication_date.isoformat(),  # Конвертируем в строку для JSON
        "author_id": news.author_id
    }
    cache_news(news_id, news_dict)

    return news


@app.get("/comments/", response_model=List[CommentResponse])
def read_comments(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Comment).offset(skip).limit(limit).all()


@app.get("/news/", response_model=List[NewsResponse])
def read_news(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(News).offset(skip).limit(limit).all()


@app.post("/make-me-author")
def make_me_author(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Сделать текущего пользователя автором (для тестирования)"""
    user.is_verified_author = True
    db.commit()
    return {"message": "Теперь вы автор! Можете создавать новости"}


# Аутентификация endpoints
@app.post("/register", response_model=UserResponse)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(400, "Email already registered")

    hashed_password = get_password_hash(user_data.password)
    user = User(
        name=user_data.name,
        email=user_data.email,
        hashed_password=hashed_password
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/refresh", response_model=Token)
def refresh_token(request: RefreshRequest, db: Session = Depends(get_db)):
    # Ищем сессию в Redis
    session_data = get_cached_session(request.refresh_token)

    if not session_data:
        raise HTTPException(401, "Invalid refresh token")

    user_id = session_data["user_id"]

    # Создаем новые токены
    access_token = create_access_token(data={"sub": user_id})
    new_refresh_token = create_refresh_token()

    # Обновляем сессию в Redis
    cache_session(new_refresh_token, user_id)

    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer"
    }


@app.post("/login", response_model=Token)
def login(user_data: UserLogin, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == user_data.email).first()
    if not user or not verify_password(user_data.password, user.hashed_password):
        raise HTTPException(401, "Invalid credentials")

    access_token = create_access_token(data={"sub": user.id})
    refresh_token = create_refresh_token()
    cache_session(refresh_token, user.id)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }


# endpoints
@app.post("/news/", response_model=NewsResponse)
def create_news(news: NewsCreate, user: User = Depends(require_author), db: Session = Depends(get_db)):
    db_news = News(
        title=news.title,
        content=news.content,
        cover_image=news.cover_image,
        author_id=user.id
    )
    db.add(db_news)
    db.commit()
    db.refresh(db_news)

    # Отправляем задачу в Celery для уведомлений
    celery_app.send_task('celery_app.send_news_notification', args=[db_news.id])
    
    return db_news

@app.post("/comments/", response_model=CommentResponse)
def create_comment(comment: CommentCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db_comment = Comment(
        text=comment.text,
        news_id=comment.news_id,
        author_id=user.id
    )
    db.add(db_comment)
    db.commit()
    db.refresh(db_comment)
    return db_comment


@app.put("/news/{news_id}", response_model=NewsResponse)
def update_news(
        news: NewsBase,
        db_news: News = Depends(get_news_with_permission),  # Используем резолвер
        db: Session = Depends(get_db)
):
    for field, value in news.dict().items():
        setattr(db_news, field, value)
    db.commit()
    db.refresh(db_news)
    return db_news


@app.delete("/news/{news_id}")
def delete_news(
        db_news: News = Depends(get_news_with_permission),  # Используем резолвер
        db: Session = Depends(get_db)
):
    db.delete(db_news)
    db.commit()
    return {"message": "News deleted"}


@app.put("/comments/{comment_id}", response_model=CommentResponse)
def update_comment(
        comment: CommentBase,
        db_comment: Comment = Depends(get_comment_with_permission),  # Используем резолвер
        db: Session = Depends(get_db)
):
    db_comment.text = comment.text
    db.commit()
    db.refresh(db_comment)
    return db_comment


@app.delete("/comments/{comment_id}")
def delete_comment(
        db_comment: Comment = Depends(get_comment_with_permission),  # Используем резолвер
        db: Session = Depends(get_db)
):
    db.delete(db_comment)
    db.commit()
    return {"message": "Comment deleted"}


@app.get("/")
def root():
    return {"message": "News API with Auth is running!"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
