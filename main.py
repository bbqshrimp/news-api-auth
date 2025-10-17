from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from sqlalchemy.sql import func
from pydantic import BaseModel
from typing import Optional, List, Any
import datetime

# Database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    registration_date = Column(DateTime, default=datetime.datetime.utcnow)
    is_verified_author = Column(Boolean, default=False)
    avatar = Column(String, nullable=True)
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

# Create tables
Base.metadata.create_all(bind=engine)

# Schemas
class UserBase(BaseModel):
    name: str
    email: str
    is_verified_author: bool = False
    avatar: Optional[str] = None

class UserCreate(UserBase):
    pass

class User(UserBase):
    id: int
    registration_date: datetime.datetime
    class Config: from_attributes = True

class NewsBase(BaseModel):
    title: str
    content: str
    cover_image: Optional[str] = None

class NewsCreate(NewsBase):
    author_id: int

class News(NewsBase):
    id: int
    publication_date: datetime.datetime
    author: User
    class Config: from_attributes = True

class CommentBase(BaseModel):
    text: str

class CommentCreate(CommentBase):
    news_id: int
    author_id: int

class Comment(CommentBase):
    id: int
    publication_date: datetime.datetime
    news_id: int
    author: User
    class Config: from_attributes = True

# FastAPI app
app = FastAPI()

# User endpoints
@app.post("/users/", response_model=User)
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    db_user = User(**user.dict())
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

@app.get("/users/", response_model=List[User])
def read_users(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(User).offset(skip).limit(limit).all()

@app.get("/users/{user_id}", response_model=User)
def read_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(404, "User not found")
    return user


# News endpoints
@app.post("/news/", response_model=News)
def create_news(news: NewsCreate, db: Session = Depends(get_db)):
    author = db.query(User).filter(User.id == news.author_id).first()
    if not author: raise HTTPException(404, "Author not found")
    if not author.is_verified_author: raise HTTPException(403, "Only verified authors can create news")
    
    db_news = News(**news.dict())
    db.add(db_news)
    db.commit()
    db.refresh(db_news)
    return db_news

@app.get("/news/", response_model=List[News])
def read_news(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(News).offset(skip).limit(limit).all()

@app.put("/news/{news_id}", response_model=News)
def update_news(news_id: int, news: NewsBase, db: Session = Depends(get_db)):
    db_news = db.query(News).filter(News.id == news_id).first()
    if not db_news: raise HTTPException(404, "News not found")
    
    for field, value in news.dict().items():
        setattr(db_news, field, value)
    db.commit()
    db.refresh(db_news)
    return db_news

@app.delete("/news/{news_id}")
def delete_news(news_id: int, db: Session = Depends(get_db)):
    news = db.query(News).filter(News.id == news_id).first()
    if not news: raise HTTPException(404, "News not found")
    
    db.delete(news)
    db.commit()
    return {"message": "News deleted"}

# Comment endpoints
@app.post("/comments/", response_model=Comment)
def create_comment(comment: CommentCreate, db: Session = Depends(get_db)):
    db_comment = Comment(**comment.dict())
    db.add(db_comment)
    db.commit()
    db.refresh(db_comment)
    return db_comment

@app.get("/comments/", response_model=List[Comment])
def read_comments(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Comment).offset(skip).limit(limit).all()

@app.put("/comments/{comment_id}", response_model=Comment)
def update_comment(comment_id: int, comment: CommentBase, db: Session = Depends(get_db)):
    db_comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not db_comment: raise HTTPException(404, "Comment not found")
    
    db_comment.text = comment.text
    db.commit()
    db.refresh(db_comment)
    return db_comment

@app.get("/")
def root():
    return {"message": "News API is running!"}


