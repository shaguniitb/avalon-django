from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    # --- Authentication URLs ---
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('register/', views.register, name='register'),
    
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
    path('room/<str:room_code>/play_again/', views.play_again, name='play_again'),
    path('room/<str:room_code>/end/', views.end_game, name='end_game'),
    path('room/<str:room_code>/join/', views.join_as_player, name='join_as_player'),
    path('room/<str:room_code>/delete/', views.delete_game, name='delete_game'),
    path('room/<str:room_code>/lady/', views.use_lady, name='use_lady'),
    path('room/<str:room_code>/advance/', views.advance_reveal, name='advance_reveal'),
    path('stats/<str:username>/', views.player_statistics, name='player_statistics'),
    path('profile/', views.profile, name='profile'),
]