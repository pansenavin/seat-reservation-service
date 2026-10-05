# Paytm Seat Reservation

A clean Django foundation for a future seat reservation REST API. This initial version includes only the project configuration, Django admin, and a health endpoint.

## Technology Stack

- Python 3.12+
- Django 5.x and Django REST Framework
- PostgreSQL with psycopg
- Docker, Docker Compose, and Adminer

## Project Structure

```text
app/          Django settings and entry points
manage.py     Django management command
Dockerfile    Django development image
docker-compose.yml  Django, PostgreSQL, and Adminer services
```

## Local Development

Create the environment file from the example:

```powershell
Copy-Item .env.example .env
```

Start the project:

```sh
docker compose up --build
```

The Django development server is available at http://localhost:8000. Compose starts PostgreSQL and Adminer alongside it; Django connects to PostgreSQL at `db`, the Compose service name.

## Database Setup

Run Django migrations:

```sh
docker compose exec web python manage.py migrate
```

Create a Django admin superuser:

```sh
docker compose exec web python manage.py createsuperuser
```

## Endpoints

- Health check: `GET` http://localhost:8000/api/health/
- Django admin: http://localhost:8000/admin/
- Adminer: http://localhost:8080/

To connect in Adminer, select **PostgreSQL**, use server `db`, username `postgres`, password `postgres`, and database `seat_reservation` (or the values configured in `.env`).