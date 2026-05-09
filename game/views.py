from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.contrib.auth import login
from .models import GameRoom, Player
from django.views.decorators.http import require_POST
from django.http import JsonResponse

from django.contrib import messages
from .logic import start_game_and_assign_roles, get_player_knowledge, tally_team_votes, tally_quest_votes, attempt_assassination, MISSION_RULES, get_required_team_size

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

def broadcast_game_update(room_code):
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'game_{room_code}',
        {
            'type': 'game_update'
        }
    )


def home(request):
    if request.method == "POST":
        username = request.POST.get("username")
        room_code = request.POST.get("room_code").upper()
        
        # For simplicity right now, we will auto-create and log in the user
        user, created = User.objects.get_or_create(username=username)
        login(request, user)
        
        # Get or create the room
        room, room_created = GameRoom.objects.get_or_create(
            room_code=room_code, 
            defaults={'host': user} # Only sets the host if creating a new room
        )
            
        # Add the player to the game
        Player.objects.get_or_create(user=user, game=room)
        
        # Send them to the game room URL
        return redirect('game_room', room_code=room_code)
        
    return render(request, 'game/home.html')

def game_room(request, room_code):
    # Fetch the room and ensure the current user is actually in it
    room = get_object_or_404(GameRoom, room_code=room_code)
    
    try:
        player = Player.objects.get(user=request.user, game=room)
    except Player.DoesNotExist:
        return redirect('home')
    
    knowledge_data = get_player_knowledge(room, player)

    all_players = room.players.all()
    voted_count = all_players.filter(has_voted=True).count()
    quest_voted_count = all_players.filter(has_quest_voted=True).count()

    required_team_size = get_required_team_size(all_players.count(), room.current_round)
    
    required_fails = 1
    if all_players.count() >= 7 and room.current_round == 4:
        required_fails = 2
    
    context = {
        'room': room,
        'player': player,
        'all_players': all_players,
        'knowledge': knowledge_data["text"],
        'known_player_ids': knowledge_data["ids"],
        'voted_count': voted_count, # Pass the count to the template
        'quest_voted_count': quest_voted_count, 
        'required_team_size': required_team_size, # Passed to template
        'required_fails': required_fails,         # Passed to template
    }
    return render(request, 'game/room.html', context)

def start_game(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)
    
    # Security check: Must be a POST request, must be the host, must be in the LOBBY
    if request.method == "POST" and request.user == room.host and room.current_phase == 'LOBBY':
        try:

            # Dynamically grab the list of checked roles from the form
            selected_roles = request.POST.getlist('special_roles')
            
            # Pass the dynamic list to your logic engine
            start_game_and_assign_roles(room, special_roles=selected_roles)
            
            # Broadcast the update so everyone's screen refreshes
            broadcast_game_update(room_code)
        except ValueError as e:
            # If the host picks too many evil roles for the player count, 
            # your logic engine will throw an error and we display it here!
            messages.error(request, str(e))
            
    return redirect('game_room', room_code=room_code)

@require_POST
def propose_team(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)
    player = get_object_or_404(Player, user=request.user, game=room)

    # 1. Validation: Only the leader can propose
    if player != room.current_leader:
        return redirect('game_room', room_code=room_code)

    # 2. Validation: Check team size (Optional but recommended)
    current_player_count = room.players.count()
    required_size = get_required_team_size(current_player_count, room.current_round)
    if room.proposed_team.count() != required_size:
        return redirect('game_room', room_code=room_code)

    # 3. ADVANCE THE PHASE
    # Make sure this matches the string your template/JavaScript expects
    room.current_phase = "TEAM_VOTING" 
    room.save()

    return redirect('game_room', room_code=room_code)

def cast_vote(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)
    current_player = get_object_or_404(Player, user=request.user, game=room)

    if request.method == "POST" and room.current_phase == 'TEAM_VOTING' and not current_player.has_voted:
        # Get the button value ('approve' or 'reject')
        vote_choice = request.POST.get('vote_choice')
        
        # Save the player's vote
        current_player.has_voted = True
        current_player.vote_approve = (vote_choice == 'approve')
        current_player.save()

        # Check if everyone has voted!
        total_players = room.players.count()
        votes_cast = room.players.filter(has_voted=True).count()
        
        if votes_cast == total_players:
            # Extract a simple list of booleans (True for approve, False for reject)
            votes = list(room.players.values_list('vote_approve', flat=True))
            
            # Reset all players' voting status for the next round
            room.players.update(has_voted=False, vote_approve=None)
            
            # Pass the votes to the logic engine we built earlier!
            tally_team_votes(room, votes)
            broadcast_game_update(room_code)

    return redirect('game_room', room_code=room_code)

def cast_quest_vote(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)
    current_player = get_object_or_404(Player, user=request.user, game=room)

    if request.method == "POST" and room.current_phase == 'QUESTING' and not current_player.has_quest_voted:
        
        # Security check: Make sure this player is actually on the mission!
        if current_player in room.proposed_team.all():
            vote_choice = request.POST.get('quest_vote')
            
            # Save the vote
            current_player.has_quest_voted = True
            current_player.quest_vote_success = (vote_choice == 'success')
            current_player.save()

            # Check if all team members have voted
            team_size = room.proposed_team.count()
            votes_cast = room.players.filter(has_quest_voted=True).count()
            
            if votes_cast == team_size:
                # Extract the boolean votes
                votes = list(room.players.filter(has_quest_voted=True).values_list('quest_vote_success', flat=True))
                
                # Reset quest voting status for the future
                room.players.update(has_quest_voted=False, quest_vote_success=None)
                
                # Tally the results
                tally_quest_votes(room, votes)
                broadcast_game_update(room_code)

    return redirect('game_room', room_code=room_code)

def assassinate(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)
    current_player = get_object_or_404(Player, user=request.user, game=room)

    # Security check: Must be POST, must be ASSASSIN_PHASE, and the user MUST be the Assassin
    if request.method == "POST" and room.current_phase == 'ASSASSIN_PHASE' and current_player.role == 'Assassin':
        target_id = request.POST.get('target_player')
        
        if target_id:
            attempt_assassination(room, target_id)
            broadcast_game_update(room_code)

    return redirect('game_room', room_code=room_code)

def leave_game(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)
    
    try:
        player = Player.objects.get(user=request.user, game=room)
        
        # If the host leaves, destroy the entire room
        if request.user == room.host:
            room.delete()
            # We don't broadcast here because the room is gone. 
            # The WebSocket will naturally close or error out for others.
        else:
            player.delete()
            broadcast_game_update(room_code) # Tell everyone else to refresh the lobby
            
    except Player.DoesNotExist:
        pass

    return redirect('home')

def kick_player(request, room_code, player_id):
    room = get_object_or_404(GameRoom, room_code=room_code)
    
    # Security: Only the host can kick, and only during the LOBBY phase
    if request.user == room.host and room.current_phase == 'LOBBY':
        try:
            target_player = Player.objects.get(id=player_id, game=room)
            # Make sure the host doesn't kick themselves
            if target_player.user != room.host:
                target_player.delete()
                broadcast_game_update(room_code)
        except Player.DoesNotExist:
            pass

    return redirect('game_room', room_code=room_code)

@require_POST
def toggle_player_selection(request, room_code, player_id):
    room = get_object_or_404(GameRoom, room_code=room_code)
    
    # Verify the requester is actually the host/leader
    if request.user != room.current_leader.user:
        return JsonResponse({'status': 'unauthorized'}, status=403)
        
    if room.current_phase != 'TEAM_BUILDING':
        return JsonResponse({'status': 'wrong_phase'}, status=400)

    target_player = get_object_or_404(Player, id=player_id)
    
    if target_player in room.proposed_team.all():
        room.proposed_team.remove(target_player)
        action = "removed"
    else:
        room.proposed_team.add(target_player)
        action = "added"
        
    return JsonResponse({'status': 'success', 'action': action})
