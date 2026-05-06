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

## Production deployment (Docker Compose)
### Deploy in production with `docker-compose.prod.yml`
1. Clone the repository.
2. Create runtime env file from template.
3. Set production values (`DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, etc.).
4. Build and start containers.
5. Verify app health.
6. Manage updates safely.

```bash
cp .env.example .env
```

Edit `.env` and set at least:
- `DJANGO_SECRET_KEY` (strong random value)
- `DJANGO_DEBUG=False`
- `DJANGO_ALLOWED_HOSTS=your-domain.com,www.your-domain.com`
- `APP_PORT=8000` (or preferred external port)

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Check status and logs:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f web
```

Run one-off commands:

```bash
docker compose -f docker-compose.prod.yml run --rm web python manage.py migrate
docker compose -f docker-compose.prod.yml run --rm web python manage.py check
docker compose -f docker-compose.prod.yml run --rm web python manage.py test elections
```

Stop or restart:

```bash
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d
```

### Notes
- Persistent SQLite data is stored in Docker volume `sqlite_data`.
- Keep `.env` out of version control.
- For public internet exposure, put a reverse proxy (Nginx/Caddy) with TLS in front.
