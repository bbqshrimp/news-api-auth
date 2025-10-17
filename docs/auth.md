# Authentication System Documentation

## Ролевая модель

### Роли пользователей:

- User (обычный пользователь)
  - Комментирование новостей
  - Редактирование своих комментариев
  - Удаление своих комментариев
  - Просмотр новостей и комментариев

- Author (верифицированный автор) - is_verified_author = True
  - Все возможности User
  - Создание новостей
  - Редактирование своих новостей  
  - Удаление своих новостей

- Admin (администратор) - is_admin = True
  - Все возможности Author
  - Редактирование любых новостей
  - Удаление любых новостей
  - Редактирование любых комментариев
  - Удаление любых комментариев

## Endpoints аутентификации

### Основная аутентификация:
- POST /register - регистрация нового пользователя
- POST /login - вход в систему (получение JWT токенов)
- POST /refresh - обновление access токена
- POST /logout - выход из системы
- GET /my-sessions - просмотр активных сессий
- GET /me - информация о текущем пользователе

### GitHub OAuth:
- GET /github/login - авторизация через GitHub (требует настройки credentials)
- GET /github/callback - callback endpoint для GitHub OAuth

### Управление правами (для тестирования):
- POST /make-me-author - сделать текущего пользователя автором

## Endpoints новостей

- GET /news/ - получение списка всех новостей
- POST /news/ - создание новости (требует роль Author или Admin)
- PUT /news/{news_id} - редактирование новости (только автор или админ)
- DELETE /news/{news_id} - удаление новости (только автор или админ)

## Endpoints комментариев

- GET /comments/ - получение списка всех комментариев  
- POST /comments/ - создание комментария (требует авторизацию)
- PUT /comments/{comment_id} - редактирование комментария (только автор комментария или админ)
- DELETE /comments/{comment_id} - удаление комментария (только автор комментария или админ)

## Механизм авторизации

### JWT Tokens:
- Access Token - короткоживущий (30 минут), для доступа к защищенным endpoints
- Refresh Token - долгоживущий (7 дней), для обновления access token
- Refresh Sessions - хранятся в БД с user_agent для отслеживания активных сессий

### Зависимости авторизации:
- get_current_user - проверка JWT токена и получение пользователя
- require_author - проверка что пользователь является автором
- require_admin - проверка прав администратора
- get_news_with_permission - резолвер для проверки прав на конкретную новость
- get_comment_with_permission - резолвер для проверки прав на конкретный комментарий

## Технические детали

### Безопасность:
- Пароли хэшируются с использованием Argon2
- JWT токены подписываются секретным ключом
- Refresh токены хранятся в базе данных
- Проверка user_agent для сессий

### База данных:
- PostgreSQL для production
- SQLAlchemy ORM для работы с БД
- Автоматическое создание таблиц при старте

### Настройки окружения:
```env
SECRET_KEY=your-secret-key
DATABASE_URL=postgresql://user:password@localhost/dbname
GITHUB_CLIENT_ID=your-github-client-id
GITHUB_CLIENT_SECRET=your-github-client-secret
