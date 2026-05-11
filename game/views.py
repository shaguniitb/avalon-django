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
    room = get_object_or_404(GameRoom, room_code=room_code)
    try:
        player = Player.objects.get(user=request.user, game=room)
    except Player.DoesNotExist:
        return redirect('home')
    
    knowledge_data = get_player_knowledge(room, player)
    all_players = room.players.all().order_by('seat_order', 'id')
    num_players = all_players.count()
    
    # --- BUILD THE 5-SQUARE TRACK IN PYTHON ---
    missions = {m.round_number: m for m in room.missions.all()}
    mission_track = []
    
    for r in range(1, 6):
        size = get_required_team_size(num_players, r) if num_players >= 5 else 0
        status = 'pending'
        
        # Check the database for the results of this round
        if r in missions:
            status = 'success' if missions[r].did_succeed else 'fail'
            
        mission_track.append({
            'round': r,
            'size': size,
            'status': status
        })

# --- CALCULATE THE HAMMER PLAYER ---
    hammer_player = None
    if room.current_phase != 'LOBBY' and room.current_leader and num_players > 0:
        current_seat = room.current_leader.seat_order
        failed_votes = room.failed_votes
        
        # Calculate who will make the 5th proposal (loops around the table using modulo)
        hammer_seat = (current_seat + (4 - failed_votes) - 1) % num_players + 1
        hammer_player = all_players.filter(seat_order=hammer_seat).first()

    proposals = room.history_proposals.all().order_by('id')
    history_headers = []
    attempt_numbers = []
    player_history = []

    if proposals.exists():
        # 1. Build the dynamic headers with colspan
        current_round = None
        count = 0
        attempt = 1
        for prop in proposals:
            if prop.round_number != current_round:
                if current_round is not None:
                    history_headers.append({'round': current_round, 'colspan': count})
                current_round = prop.round_number
                count = 1
                attempt = 1
            else:
                count += 1
                attempt += 1
            attempt_numbers.append(attempt)
            
        if current_round is not None:
            history_headers.append({'round': current_round, 'colspan': count})
            
        # 2. Build each player's row data
        for p in all_players:
            p_votes = []
            for prop in proposals:
                on_team = prop.team.filter(id=p.id).exists()
                if prop.approves.filter(id=p.id).exists():
                    vote = 'approve'
                elif prop.rejects.filter(id=p.id).exists():
                    vote = 'reject'
                else:
                    vote = 'none'

                is_leader = (prop.leader == p) if prop.leader else False
                p_votes.append({'on_team': on_team, 'vote': vote, 'is_leader': is_leader})
                
            player_history.append({'player': p, 'votes': p_votes})
        

    context = {
        'room': room,
        'player': player,
        'all_players': all_players,
        'knowledge': knowledge_data["text"],
        'known_player_ids': knowledge_data["ids"],
        'mission_track': mission_track, 
        'hammer_player': hammer_player,   
        'history_headers': history_headers,
        'attempt_numbers': attempt_numbers,
        'player_history': player_history,
    }
    return render(request, 'game/room.html', context) # (or 'game/room.html' depending on your setup)

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

    if player == room.current_leader:
        # Clear out the previous round's vote displays when a new team is proposed
        for p in room.players.all():
            p.last_vote = None
            p.save()

        room.current_phase = "TEAM_VOTING"
        room.save()

    return redirect('game_room', room_code=room_code)

@require_POST
def cast_vote(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)
    player = get_object_or_404(Player, user=request.user, game=room)

    vote_choice = request.POST.get('vote_choice')
    if vote_choice == 'approve':
        player.vote_approve = True
        player.last_vote = 'Approve'
    else:
        player.vote_approve = False
        player.last_vote = 'Reject'
    
    player.has_voted = True
    player.save()

    # If everyone has voted, tally them (handled by your logic.py usually)
    if room.players.filter(has_voted=False).count() == 0:
        votes = list(room.players.values_list('vote_approve', flat=True))
        tally_team_votes(room, votes)
        room.players.update(has_voted=False, vote_approve=None)

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

@require_POST
def assassinate(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)
    player = get_object_or_404(Player, user=request.user, game=room)

    if room.current_phase == 'ASSASSIN_PHASE' and player.role == 'Assassin':
        # Get all selected checkboxes from the form
        target_ids = request.POST.getlist('assassin_targets')
        
        # Ensure they picked either exactly 1 or exactly 2 people
        if len(target_ids) in [1, 2]:
            attempt_assassination(room, target_ids)
            # broadcast_game_update(room_code) # If using channels
            
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
