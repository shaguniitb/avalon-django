web: daphne avalon_project.asgi:application
python manage.py collectstatic --noinput && daphne your_project_name.asgi:application --port $PORT --bind 0.0.0.0
