# System wyborczy (Django)
Aplikacja webowa do obsługi procesu wyborczego: role i uprawnienia (RBAC), zarządzanie wyborami, kandydatami, kalendarzem, wynikami i panelem administracyjnym.

## Najważniejsze funkcje
- Rejestracja, logowanie i wylogowanie użytkowników.
- RBAC (role: `ADMIN`, `USER`, `AUDITOR`) z kontrolą dostępu i ukrywaniem niedozwolonych zakładek.
- Zarządzanie wyborami, kandydatami, komitetami i cyklem życia wyborów.
- Kalendarz wyborów z filtrami i pickerem dat.
- Wyniki oraz API analityczne (m.in. top frekwencja, trendy historyczne).
- Generowanie dokumentów PDF z wynikami.

## Wymagania
- Docker + Docker Compose

## Uruchomienie lokalne (Docker)
```bash
docker compose up -d --build
```

Interfejsy:
- Aplikacja: `http://localhost:8000`
- GUI bazy SQLite (`sqlite-web`): `http://localhost:8081`

Zatrzymanie:
```bash
docker compose down
```

## Baza danych i podgląd rekordów
Projekt używa SQLite (plik w wolumenie: `/data/db.sqlite3`).

Szybki podgląd przez CLI:
```bash
docker compose exec web sqlite3 /data/db.sqlite3
```

Przykładowe komendy SQL:
```sql
.tables
SELECT * FROM elections_election LIMIT 20;
```

## Seed danych (bootstrap)
Komenda:
- prod-safe: `python manage.py bootstrap_data --env prod`
- lokalne dane demo: `python manage.py bootstrap_data --env local --with-demo`

W Docker:
```bash
docker compose run --rm web python manage.py bootstrap_data --env local --with-demo
```

Skrypty pomocnicze:
- `scripts/setup-local.sh` – migrate + lokalny seed + check
- `scripts/setup-prod.sh` – migrate + prod seed + check

Domyślne hasła seedera (można nadpisać env):
- `BOOTSTRAP_ADMIN_PASSWORD` (domyślnie `admin12345`)
- `BOOTSTRAP_DEMO_PASSWORD` (domyślnie `demo12345`)

## Testy i kontrola jakości
```bash
docker compose run --rm web python manage.py check
docker compose run --rm web python manage.py test elections
```

## Diagram ERD
Pełny diagram ERD modeli domenowych:
- `docs/ERD_FULL.md`

## Produkcja (docker-compose.prod.yml)
1. Utwórz `.env` na bazie `.env.example`.
2. Ustaw co najmniej:
   - `DJANGO_SECRET_KEY`
   - `DJANGO_DEBUG=False`
   - `DJANGO_ALLOWED_HOSTS`
   - `APP_PORT`
3. Uruchom:

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Przydatne:
```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f web
```

## Deploy na Render
Repo zawiera `render.yaml` (Blueprint):
- build: instalacja zależności + `collectstatic`
- start: `migrate` + `bootstrap_data --env prod` + `gunicorn`
- trwały dysk dla SQLite: `/var/data`

## Kluczowe ścieżki w projekcie
- Modele: `elections/models.py`
- Widoki: `elections/views.py`, `elections/api_views.py`
- Routing: `elections/urls.py`
- RBAC: `elections/rbac.py`
- Szablony: `elections/templates/elections/`
- Style: `elections/static/elections/css/style.css`