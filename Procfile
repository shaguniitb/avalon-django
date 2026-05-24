web: daphne avalon_project.asgi:application
web: python manage.py collectstatic --noinput && daphne avalon_project.asgi:application -p $PORT -b 0.0.0.0
