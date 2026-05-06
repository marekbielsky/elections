# Elections Django App
## Run with Docker
1. Build and start:
docker compose up --build
2. App URL:
http://127.0.0.1:8000
3. Stop containers:
docker compose down
## Useful commands
- Run migrations manually:
docker compose run --rm web python manage.py migrate
- Run tests:
docker compose run --rm web python manage.py test elections
