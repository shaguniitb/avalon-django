from django.contrib import admin
from .models import GameRoom, Player, Mission

# Register your models here so they appear in the admin dashboard
admin.site.register(GameRoom)
admin.site.register(Player)
admin.site.register(Mission)
