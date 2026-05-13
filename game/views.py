from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.db import transaction
from .models import GameRoom, Player
from .logic import start_game_and_assign_roles, get_player_knowledge, tally_team_votes, tally_quest_votes, attempt_assassination, MISSION_RULES, get_required_team_size

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

import random
import string

def register(request):
    # If the user is already logged in, redirect them to the home page
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            auth_login(request, user)  # Automatically log them in after signing up
            return redirect('home')
    else:
        form = UserCreationForm()
        
    return render(request, 'register.html', {'form': form})

def broadcast_game_update(room_code):
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'game_{room_code}',
        {
            'type': 'game_update'
        }
    )

@login_required
def home(request):
    if request.method == "POST":
        action = request.POST.get("action")
        user = request.user        
        
        # --- ACTION 1: CREATE A NEW LOBBY ---
        if action == "create":
            # Generate a random 5-letter room code that doesn't exist yet
            while True:
                room_code = ''.join(random.choices(string.ascii_uppercase, k=5))
                if not GameRoom.objects.filter(room_code=room_code).exists():
                    break
            
            # Create the room and add the host as the first player
            room = GameRoom.objects.create(room_code=room_code, host=user)
            Player.objects.create(user=user, game=room)
            
            # Tell the lobby browser to update if anyone is looking at it
            broadcast_game_update(room_code) 
            return redirect('game_room', room_code=room_code)

        # --- ACTION 2: JOIN AN EXISTING LOBBY ---
        elif action == "join":
            room_code = request.POST.get("room_code")
            room = get_object_or_404(GameRoom, room_code=room_code)

            # Security: Prevent joining twice in different browsers
            existing_player = Player.objects.filter(user=user, game=room).first()
            if not existing_player:
                Player.objects.create(user=user, game=room)
                broadcast_game_update(room_code)
                        
            return redirect('game_room', room_code=room_code)
        
    # Fetch all rooms that are currently waiting for players (LOBBY phase)
    available_lobbies = GameRoom.objects.filter(current_phase='LOBBY').prefetch_related('players__user', 'host')
    
    return render(request, 'game/home.html', {'available_lobbies': available_lobbies})


@login_required
def game_room(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)
    try:
        player = Player.objects.get(user=request.user, game=room)
    except Player.DoesNotExist:
        return redirect('home')
    
    knowledge_data = get_player_knowledge(room, player)
    all_players = room.players.all().order_by('seat_order', 'id')

    proposals = room.history_proposals.prefetch_related('team', 'approves', 'rejects').order_by('id')

    history_headers = []
    attempt_numbers = []
    player_history = []

    if proposals.exists():
        current_round = None
        count = 0
        attempt = 1
        
        # 2. FIX: Pre-compute the IDs into Python sets for lightning-fast O(1) memory lookups
        proposal_data_cache = []
        
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
            
            # Cache the IDs in memory. Because we used prefetch_related, `.all()` does NOT hit the DB here.
            proposal_data_cache.append({
                'leader_id': prop.leader_id if prop.leader else None,
                'team_ids': {p.id for p in prop.team.all()},
                'approve_ids': {p.id for p in prop.approves.all()},
                'reject_ids': {p.id for p in prop.rejects.all()},
            })
            
        if current_round is not None:
            history_headers.append({'round': current_round, 'colspan': count})
            
        # 3. FIX: Loop through the in-memory cache instead of querying the database
        for p in all_players:
            p_votes = []
            for prop_data in proposal_data_cache:
                
                # Check Python sets instead of .filter().exists()
                on_team = p.id in prop_data['team_ids']
                
                if p.id in prop_data['approve_ids']:
                    vote = 'approve'
                elif p.id in prop_data['reject_ids']:
                    vote = 'reject'
                else:
                    vote = 'none'

                is_leader = (prop_data['leader_id'] == p.id)
                p_votes.append({'on_team': on_team, 'vote': vote, 'is_leader': is_leader})
                
            player_history.append({'player': p, 'votes': p_votes})            

    context = {
        'room': room,
        'player': player,
        'all_players': all_players,
        'knowledge': knowledge_data["text"],
        'known_player_ids': knowledge_data["ids"],
        'mission_track': getattr(room, 'mission_track', None), 
        'hammer_player': getattr(room, 'hammer_player', None),   
        'history_headers': history_headers,
        'attempt_numbers': attempt_numbers,
        'player_history': player_history,
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

    if player == room.current_leader:
        # Clear out the previous round's vote displays when a new team is proposed
        for p in room.players.all():
            p.last_vote = None
            p.save()

        room.current_phase = "TEAM_VOTING"
        room.save()
        broadcast_game_update(room_code)

    return redirect('game_room', room_code=room_code)

@require_POST
def cast_vote(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)

    with transaction.atomic():
        locked_room = GameRoom.objects.select_for_update().get(id=room.id)
        player = get_object_or_404(Player, user=request.user, game=locked_room)

        if player.has_voted:
            return redirect('game_room', room_code=room_code)        
        
        vote_choice = request.POST.get('vote_choice')
        if vote_choice == 'approve':
            player.vote_approve = True
            player.last_vote = 'Approve'
        else:
            player.vote_approve = False
            player.last_vote = 'Reject'

        player.has_voted = True
        player.save()

        # Check if everyone has voted
        if locked_room.players.filter(has_voted=False).count() == 0:
            votes = list(locked_room.players.values_list('vote_approve', flat=True))
            tally_team_votes(locked_room, votes)
            locked_room.players.update(has_voted=False, vote_approve=None)

            # EVERYONE VOTED: Do a full broadcast to reveal results
            broadcast_game_update(room_code)            
        else:
            # NOT EVERYONE VOTED: Send a targeted event to update UI without reloading
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'game_{room_code}',
                {
                    'type': 'game_update',
                    'event_type': 'player_voted',
                    'player_id': player.id
                }
            )

    return redirect('game_room', room_code=room_code)

@require_POST
def cast_quest_vote(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)
    
    with transaction.atomic():
        locked_room = GameRoom.objects.select_for_update().get(id=room.id)
        current_player = get_object_or_404(Player, user=request.user, game=locked_room)

        if locked_room.current_phase == 'QUESTING' and not current_player.has_quest_voted:
            if current_player in locked_room.proposed_team.all():
                vote_choice = request.POST.get('quest_vote')
                current_player.has_quest_voted = True

                if current_player.is_good:
                    current_player.quest_vote_success = True
                else:
                    current_player.quest_vote_success = (vote_choice == 'success')
                
                current_player.save()

                team_size = locked_room.proposed_team.count()
                votes_cast = locked_room.players.filter(has_quest_voted=True).count()
                
                if votes_cast == team_size:
                    votes = list(locked_room.players.filter(has_quest_voted=True).values_list('quest_vote_success', flat=True))
                    locked_room.players.update(has_quest_voted=False, quest_vote_success=None)
                    tally_quest_votes(locked_room, votes)
                    
                    # MISSION COMPLETE: Full broadcast to reveal results
                    broadcast_game_update(room_code)
                else:
                    # MISSION ONGOING: Send a targeted event
                    channel_layer = get_channel_layer()
                    async_to_sync(channel_layer.group_send)(
                        f'game_{room_code}',
                        {
                            'type': 'game_update',
                            'event_type': 'player_voted',
                            'player_id': current_player.id
                        }
                    )

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
            broadcast_game_update(room_code) # If using channels
            
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

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'game_{room_code}',
        {
            'type': 'game_update',             # Still routes to the same consumer method
            'event_type': 'player_toggled',    # Custom flag to tell JS what to do
            'player_id': player_id,
            'action': action
        }
    )        
        
    return JsonResponse({'status': 'success', 'action': action})
