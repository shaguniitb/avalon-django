from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('room/<str:room_code>/', views.game_room, name='game_room'),
    path('room/<str:room_code>/start/', views.start_game, name='start_game'),
    path('room/<str:room_code>/propose/', views.propose_team, name='propose_team'),
    path('room/<str:room_code>/vote/', views.cast_vote, name='cast_vote'),
    path('room/<str:room_code>/quest/', views.cast_quest_vote, name='cast_quest_vote'),
    path('room/<str:room_code>/assassinate/', views.assassinate, name='assassinate'),
    path('room/<str:room_code>/leave/', views.leave_game, name='leave_game'),
    path('room/<str:room_code>/kick/<int:player_id>/', views.kick_player, name='kick_player'),
    path('room/<str:room_code>/toggle_player/<int:player_id>/', views.toggle_player_selection, name='toggle_player'),
]