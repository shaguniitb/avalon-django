from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse, request
from django.db import transaction
from django.db.models import Count
from django.db import models
from .models import GameRoom, Player, Spectator
from .logic import start_game_and_assign_roles, get_player_knowledge, tally_team_votes, tally_quest_votes, attempt_assassination, MISSION_RULES, get_required_team_size, transition_to_new_room

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

            # CATCH THE CHECKBOX DATA HERE
            allow_spoilers = request.POST.get('allow_spoilers') == 'on'            

            # Create the room and add the host as the first player
            room = GameRoom.objects.create(room_code=room_code, host=user, allow_spectator_spoilers=allow_spoilers)
            Player.objects.create(user=user, game=room)
            
            # Tell the lobby browser to update if anyone is looking at it
            broadcast_game_update(room_code) 
            return redirect('game_room', room_code=room_code)

        # --- ACTION 2: JOIN AN EXISTING LOBBY ---
        elif action == "join":
            room_code = request.POST.get("room_code")
            room = get_object_or_404(GameRoom, room_code=room_code)

            # 1. RECONNECT LOGIC: Check if this user already exists in THIS room
            existing_player = Player.objects.filter(user=user, game=room).first()
            if existing_player:
                messages.success(request, "Reconnected to your game!")
                return redirect('game_room', room_code=room_code)
                
            # 2. NEW PLAYER LOGIC: Block joining if the game already started
            if room.current_phase != 'LOBBY':
                messages.error(request, "This game has already started. You cannot join mid-game.")
                return redirect('home')

            # Create new player
            Player.objects.create(user=user, game=room)
            broadcast_game_update(room_code)
                    
            return redirect('game_room', room_code=room_code)
        
        # --- ACTION 3: SPECTATE ---
        elif action == "spectate":
            room_code = request.POST.get("room_code")
            room = get_object_or_404(GameRoom, room_code=room_code)

            # Prevent active players from becoming spectators too
            is_player = Player.objects.filter(user=user, game=room).exists()

            if not is_player:
                Spectator.objects.get_or_create(user=user, game=room)

            # broadcast_game_update(room_code)                

            return redirect('game_room', room_code=room_code)        
        
    # Fetch all rooms that are currently waiting for players (LOBBY phase)
    available_lobbies = GameRoom.objects.filter(
        current_phase='LOBBY'
        ).annotate(num_players=Count('players')).filter(num_players__gt=0).prefetch_related('players__user', 'host')

    active_games = GameRoom.objects.exclude(
        current_phase='LOBBY'
        ).exclude(
            players__user =request.user
        ).annotate(num_players=Count('players')).filter(num_players__gt=0).prefetch_related('players__user', 'host')
    
    # Check if the current user is already in a game so we can show a "Rejoin" button
    active_game = None
    active_player = Player.objects.filter(user=request.user, game__is_active=True).first()
    if active_player:
        active_game = active_player.game
    
    return render(request, 'game/home.html', {
        'available_lobbies': available_lobbies,
        'active_games': active_games,
        'active_game': active_game, # Pass the active game to the template
    })


@login_required
def game_room(request, room_code):
    # Safely try to find the room
    try:
        room = GameRoom.objects.get(room_code=room_code)
    except GameRoom.DoesNotExist:
        # --- NEW: Only show this message if the user IS NOT the one who deleted it ---
        # We can't check room.host because the room is gone, so we just assume 
        # that if they just created a new game, we shouldn't spam them with this error.
        messages.info(request, "That game no longer exists.")
        return redirect('home')
    
    if room.rematch_code:
        return redirect('game_room', room_code=room.rematch_code)    
    
    if not room.is_active:
        if request.user != room.host:
            messages.info(request, "The host has disbanded this room.")
        return redirect('home')

    player = Player.objects.filter(user=request.user, game=room).first()

    is_spectator = False

    if not player:
        is_spectator = Spectator.objects.filter(
            user=request.user,
            game=room
        ).exists()

        if not is_spectator:
            return redirect('home')
    
    if player and player.pending_message:
        messages.success(request, player.pending_message)
        player.pending_message = None
        player.save(update_fields=['pending_message'])


    if player:
        knowledge_data = get_player_knowledge(room, player)
    else:
        knowledge_data = {
            "text": "You are spectating this game.",
            "ids": []
        }        
    all_players = room.players.all().order_by('seat_order', 'id')

    lady_chain = []
    if room.use_lady_of_the_lake and room.current_lady_holder:
        curr = room.current_lady_holder
        
        # Walk backwards up the chain using the reverse ForeignKey manager!
        while curr:
            lady_chain.append(curr.user.username)
            curr = curr.lady_received_from.first()
            
        # Reverse the list so it displays in chronological forward order
        lady_chain.reverse()

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

    # Fetch all spectators currently in the room
    all_spectators = Spectator.objects.filter(game=room)

    spectator_spoilers = None

    # Only generate spoilers IF they are a spectator, the host allowed it, and the game has started
    if is_spectator and room.allow_spectator_spoilers and room.current_phase != 'LOBBY':
        # Create a simple dictionary mapping usernames to their true roles
        spectator_spoilers = {
            p.user.username: p.role for p in all_players
        }    

    is_lady_turn = False
    past_lady_ids = []

    if room.use_lady_of_the_lake and room.current_lady_holder:
        # 1. Who has held it? 
        # Anyone who has passed it (has a lady_target) OR currently holds it.
        past_holders = Player.objects.filter(game=room).filter(
            models.Q(lady_target__isnull=False) | models.Q(id=room.current_lady_holder.id)
        )
        past_lady_ids = list(past_holders.values_list('id', flat=True))

        # 2. Is it time to use it? (Start of Rounds 3, 4, 5 during TEAM_BUILDING)
        if room.current_phase == 'TEAM_BUILDING' and room.current_round in [3, 4, 5]:
            times_used = Player.objects.filter(game=room, lady_target__isnull=False).count()
            expected_uses = room.current_round - 2
            
            # E.g., In Round 3, expected_uses is 1. If times_used is 0, the Lady MUST act.
            if times_used < expected_uses:
                is_lady_turn = True    

    context = {
        'room': room,
        'player': player,
        'all_players': all_players,
        'spectators': all_spectators,
        'knowledge': knowledge_data["text"] if knowledge_data else None,
        'known_player_ids': knowledge_data["ids"] if knowledge_data else [],
        'mission_track': getattr(room, 'mission_track', None), 
        'hammer_player': getattr(room, 'hammer_player', None),   
        'history_headers': history_headers,
        'attempt_numbers': attempt_numbers,
        'player_history': player_history,
        'is_spectator': is_spectator,
        'spectator_spoilers': spectator_spoilers,
        'is_lady_turn': is_lady_turn,
        'past_lady_ids': past_lady_ids,
        'lady_chain': lady_chain,
    }
    return render(request, 'game/room.html', context) 

def start_game(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)
    
    # Security check: Must be a POST request, must be the host, must be in the LOBBY
    if request.method == "POST" and request.user == room.host and room.current_phase == 'LOBBY':
        try:

            # Dynamically grab the list of checked roles from the form
            selected_roles = request.POST.getlist('special_roles')
            use_lady = request.POST.get('use_lady') == 'on'

            room.players.all().update(lady_target=None)
            
            # Pass the dynamic list to your logic engine
            start_game_and_assign_roles(room, special_roles=selected_roles)

            # --- LADY INITIALIZATION ---
            room.use_lady_of_the_lake = use_lady
            if use_lady:
                last_player = room.players.order_by('seat_order').last()
                room.current_lady_holder = last_player
            room.save()

            room.players.all().update(pending_message="The game has begun! Check your secret role.")
            
            # Broadcast the update so everyone's screen refreshes
            broadcast_game_update(room_code)
        except ValueError as e:
            # If the host picks too many evil roles for the player count, 
            # your logic engine will throw an error and we display it here!
            messages.error(request, str(e))
            
    return redirect('game_room', room_code=room_code)

@require_POST
@login_required
def propose_team(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)

    # --- STRICT TIMING ENFORCEMENT ---
    if room.use_lady_of_the_lake and room.current_phase == 'TEAM_BUILDING' and room.current_round in [3, 4, 5]:
        times_used = Player.objects.filter(game=room, lady_target__isnull=False).count()
        if times_used < (room.current_round - 2):
            messages.error(request, "The Lady of the Lake must be used before a new team can be proposed.")
            return redirect('game_room', room_code=room_code)

    if not Player.objects.filter(user=request.user, game=room).exists():
        return redirect('game_room', room_code=room_code)
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
    if not Player.objects.filter(user=request.user, game=room).exists():
        return redirect('game_room', room_code=room_code)    

    # Set up flags to track what to broadcast AFTER the transaction
    should_full_refresh = False
    broadcast_player_id = None

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
            
            # Flag that we need a full refresh, but DO NOT broadcast yet!
            should_full_refresh = True
        else:
            # Flag the player ID for the silent update
            broadcast_player_id = player.id


    # --- OUTSIDE THE TRANSACTION (Database is now 100% committed and saved) ---
    
    if should_full_refresh:
        # EVERYONE VOTED: Now it is safe to tell clients to reload
        broadcast_game_update(room_code)            
    elif broadcast_player_id:
        # NOT EVERYONE VOTED: Send targeted event
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'game_{room_code}',
            {
                'type': 'game_update',
                'event_type': 'player_voted',
                'player_id': broadcast_player_id
            }
        )

    return redirect('game_room', room_code=room_code)

@require_POST
def cast_quest_vote(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)
    if not Player.objects.filter(user=request.user, game=room).exists():
        return redirect('game_room', room_code=room_code)    
    
    # 1. Set up flags to track what to broadcast AFTER the transaction
    should_full_refresh = False
    broadcast_player_id = None

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
                    
                    # Flag that we need a full refresh, but DO NOT broadcast yet!
                    should_full_refresh = True
                else:
                    # Flag the player ID for the silent update
                    broadcast_player_id = current_player.id

    # --- 2. OUTSIDE THE TRANSACTION (Database is now 100% committed and saved) ---
    if should_full_refresh:
        # MISSION COMPLETE: Full broadcast to reveal results
        broadcast_game_update(room_code)
    elif broadcast_player_id:
        # MISSION ONGOING: Send a targeted event to update UI without reloading
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'game_{room_code}',
            {
                'type': 'game_update',
                'event_type': 'player_voted',
                'player_id': broadcast_player_id
            }
        )

    return redirect('game_room', room_code=room_code)

@require_POST
@login_required
def assassinate(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)
    if not Player.objects.filter(user=request.user, game=room).exists():
        return redirect('game_room', room_code=room_code)

    player = get_object_or_404(Player, user=request.user, game=room)

    if room.current_phase == 'ASSASSIN_PHASE' and player.role == 'Assassin':
        # Get all selected checkboxes from the form
        target_ids = request.POST.getlist('assassin_targets')
        target_players = Player.objects.filter(id__in=target_ids, game=room)

        if not target_players.exists():
            messages.error(request, "Please select a valid target for assassination.")
            return redirect('game_room', room_code=room_code)        
        
        target_names = " and ".join([p.user.username for p in target_players])
        
        # Ensure they picked either exactly 1 or exactly 2 people
        if len(target_ids) in [1, 2]:
            attempt_assassination(room, target_ids)
            msg_text = f"Assassination strike locked in! The Assassin has targeted: {target_names}."
            room.players.all().update(pending_message=msg_text)
            broadcast_game_update(room_code) # If using channels
            
    return redirect('game_room', room_code=room_code)

@require_POST
@login_required
def leave_game(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)

    # --- 1. HANDLE SPECTATORS LEAVING ---
    spectator = Spectator.objects.filter(user=request.user, game=room).first()
    if spectator:
        spectator.delete()
        broadcast_game_update(room_code) # Instantly removes them from everyone's screen
        return redirect('home')    
    
    # --- 2. HANDLE PLAYERS LEAVING ---
    try:
        player = room.players.get(user=request.user)
    except Player.DoesNotExist:
        return redirect('home')

    is_host = (room.host == request.user)

    # 1. Break Foreign Key cycles manually to avoid SQLite IntegrityErrors!
    if room.current_leader == player:
        room.current_leader = None
        room.save()
        
    # Remove player from the proposed team if they are on it
    if player in room.proposed_team.all():
        room.proposed_team.remove(player)
        
    # Clear any historical TeamProposal references to this player
    player.history_led.update(leader=None)

    # 2. Transfer host ownership
    if is_host:
        # EXCLUDE the leaving player so we don't accidentally make them the new host
        new_host = room.players.exclude(id=player.id).order_by('seat_order', 'id').first()
        if new_host:
            room.host = new_host.user
            room.save()
        else:
            # If no players are left, break the final cycle and delete the room
            room.current_leader = None
            room.save()
            room.delete()
            return redirect('home')            

    # 3. Safe to delete the player now
    player.delete()
    
    # 4. Check if the room is now completely empty. If so, delete it.
    if not room.players.exists():
        room.delete()
        return redirect('home')
    
    # Broadcast to other players so their screens update
    broadcast_game_update(room_code)

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
    if not Player.objects.filter(user=request.user, game=room).exists():
        return redirect('game_room', room_code=room_code)    
    
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


@require_POST
@login_required
def play_again(request, room_code):
    old_room = get_object_or_404(GameRoom, room_code=room_code)
    
    # Security check: Only the host can start a rematch
    if request.user != old_room.host:
        return redirect('game_room', room_code=room_code)
        
    # If the rematch hasn't been generated yet, do it now using our helper
    if not old_room.rematch_code:
        transition_to_new_room(old_room)
        
    # Broadcast to the old room. This forces everyone's browser to reload via JS
    # and hit the room.rematch_code interceptor we added earlier.
    broadcast_game_update(room_code)
    
    # Redirect the host to the new room
    return redirect('game_room', room_code=old_room.rematch_code)


@require_POST
@login_required
def end_game(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)

    # 1. Only the host can end the game early
    if request.user != room.host:
        messages.error(request, "Only the host can end the game early.")
        return redirect('game_room', room_code=room_code)

    # 2. Only run this if the game is actually in progress
    if room.current_phase not in ['LOBBY', 'GOOD_WINS', 'EVIL_WINS']:
        # Mark the current game as aborted so it never gets counted in statistics
        room.current_phase = 'ABORTED'
        
        # Use our helper to create the new room and link them up
        # Note: transition_to_new_room calls room.save(), which saves our ABORTED status too
        new_code = transition_to_new_room(room)
        
        # Broadcast the update so everyone's screen instantly redirects
        broadcast_game_update(room_code)
        
        return redirect('game_room', room_code=new_code)

    return redirect('game_room', room_code=room_code)

@require_POST
@login_required
def join_as_player(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)
    
    # 1. Prevent joining if the game has already started
    if room.current_phase != 'LOBBY':
        messages.error(request, "You can only join while in the lobby.")
        return redirect('game_room', room_code=room_code)
        
    # 2. Check the Avalon player limit (Maximum 10 players)
    if room.players.count() >= 10:
        messages.error(request, "This room is full (maximum 10 players).")
        return redirect('game_room', room_code=room_code)
        
    # 3. Find and delete their Spectator record
    spectator = Spectator.objects.filter(user=request.user, game=room).first()
    if spectator:
        spectator.delete()
        
    # 4. Create their new Player record (if they aren't already one)
    if not Player.objects.filter(user=request.user, game=room).exists():
        Player.objects.create(user=request.user, game=room)
        
    # 5. Broadcast to the room so everyone's screen updates
    broadcast_game_update(room_code)
    
    return redirect('game_room', room_code=room_code)

@require_POST
@login_required
def delete_game(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)

    # Security check: Only the host can do this
    if request.user != room.host:
        messages.error(request, "Only the host can delete the game.")
        return redirect('game_room', room_code=room_code)

    if room.current_phase in ['GOOD_WINS', 'EVIL_WINS']:
        # Mark as inactive to archive it instead of deleting it
        room.is_active = False
        room.save()
        broadcast_game_update(room_code)
    else:
        # If it never finished (Lobby or aborted), it's safe to fully delete
        room.delete()


    # 2. Broadcast the update. 
    # Because the room no longer exists, when the clients reload, your game_room 
    # view will automatically redirect them all to the lobby browser!
    

    return redirect('home')

@require_POST
@login_required
def use_lady(request, room_code):
    room = get_object_or_404(GameRoom, room_code=room_code)
    player = Player.objects.filter(user=request.user, game=room).first()
    
    # Check if it's legally the Lady's turn
    if room.current_phase != 'TEAM_BUILDING' or room.current_round not in [3, 4, 5]:
        messages.error(request, "It is not the correct phase to use the Lady of the Lake.")
        return redirect('game_room', room_code=room_code)

    times_used = Player.objects.filter(game=room, lady_target__isnull=False).count()
    if times_used >= (room.current_round - 2):
        messages.error(request, "The Lady of the Lake has already been used this round.")
        return redirect('game_room', room_code=room_code)

    if player != room.current_lady_holder:
        messages.error(request, "You do not possess the Lady of the Lake.")
        return redirect('game_room', room_code=room_code)
        
    target_id = request.POST.get('lady_target')
    target_player = Player.objects.filter(id=target_id, game=room).first()
    
    if not target_player:
        messages.error(request, "Invalid target selected.")
        return redirect('game_room', room_code=room_code)
        
    # Rule Check: Target cannot be the current holder or anyone who has passed it
    if target_player == room.current_lady_holder or target_player.lady_target is not None:
        messages.error(request, "This player has already held the Lady.")
        return redirect('game_room', room_code=room_code)
        
    # 1. Update the ForeignKeys to build the chain
    player.lady_target = target_player
    player.save(update_fields=['lady_target'])
    
    room.current_lady_holder = target_player
    room.save(update_fields=['current_lady_holder'])

    # 2. Send the private result to the person who checked using pending_message
    alignment = "GOOD" if target_player.is_good else "EVIL"
    player.pending_message = f"🧜‍♀️ The Lady reveals that {target_player.user.username}'s alignment is {alignment}!"
    player.save(update_fields=['pending_message'])
    
    # 3. Broadcast public generic alert to the rest of the room
    room.players.exclude(id=player.id).update(
        pending_message=f"🧜‍♀️ {player.user.username} used the Lady of the Lake on {target_player.user.username}."
    )
    
    broadcast_game_update(room_code)
    return redirect('game_room', room_code=room_code)
    
@require_POST
@login_required
def advance_reveal(request, room_code):
    game_room = get_object_or_404(GameRoom, room_code=room_code)
    
    # Security: Ensure only the leader can click continue
    if request.user != game_room.current_leader.user:
        return redirect('game_room', room_code=room_code)

    # --- THIS IS YOUR EXACT LOGIC MOVED FROM TALLY_QUEST_VOTES ---
    if game_room.score_good >= 3:
        has_assassin = game_room.players.filter(role='Assassin').exists()
        if has_assassin:
            game_room.current_phase = 'ASSASSIN_PHASE'
            game_room.assassination_start_time = timezone.now()
        else:
            game_room.current_phase = 'GOOD_WINS'
    elif game_room.score_evil >= 3:
        game_room.current_phase = 'EVIL_WINS'
    else:
        game_room.current_round += 1
        game_room.current_phase = 'TEAM_BUILDING'
        
        # CLEAR THE PROPOSED TEAM SO YELLOW BACKGROUNDS DISAPPEAR
        game_room.proposed_team.clear()
        
        current_seat = game_room.current_leader.seat_order
        total_players = game_room.players.count()
        next_seat = (current_seat % total_players) + 1 
        game_room.current_leader = game_room.players.get(seat_order=next_seat)

    game_room.save()
    broadcast_game_update(room_code)
    return redirect('game_room', room_code=room_code)


def player_statistics(request, username):
    target_user = get_object_or_404(User, username=username)

    # Fetch only completed games. We use select_related and prefetch_related 
    # to grab all co-players in exactly 2 queries instead of hundreds.
    completed_games = Player.objects.filter(
        user=target_user,
        game__current_phase__in=['GOOD_WINS', 'EVIL_WINS']
    ).select_related('game').prefetch_related('game__players__user').order_by('-game__created_at')

    total_games = 0
    total_wins = 0

    side_stats = {'Good': {'games': 0, 'wins': 0}, 'Evil': {'games': 0, 'wins': 0}}
    role_stats = {}
    teammates = {}
    enemies = {}
    last_games = []

    for player in completed_games:
        game = player.game
        total_games += 1
        
        # Determine if this specific player won the game
        won = (player.is_good and game.current_phase == 'GOOD_WINS') or \
              (not player.is_good and game.current_phase == 'EVIL_WINS')
              
        if won:
            total_wins += 1

        # --- Side Stats ---
        side = 'Good' if player.is_good else 'Evil'
        side_stats[side]['games'] += 1
        if won: side_stats[side]['wins'] += 1

        # --- Role Stats ---
        role = player.role or "Unknown"
        if role not in role_stats:
            role_stats[role] = {'games': 0, 'wins': 0}
        role_stats[role]['games'] += 1
        if won: role_stats[role]['wins'] += 1

        # --- Last 5 Games ---
        if len(last_games) < 5:
            last_games.append({
                'date': game.created_at.strftime('%Y-%m-%d %H:%M') if game.created_at else 'Unknown',
                'role': role,
                'result': 'Win' if won else 'Loss'
            })

        # --- Teammates and Enemies ---
        for co_player in game.players.all():
            if co_player.user == target_user:
                continue # Skip themselves
                
            cp_username = co_player.user.username
            
            if co_player.is_good == player.is_good:
                # They were on the same team
                if cp_username not in teammates:
                    teammates[cp_username] = {'games': 0, 'wins': 0}
                teammates[cp_username]['games'] += 1
                if won: teammates[cp_username]['wins'] += 1
            else:
                # They were on opposite teams
                if cp_username not in enemies:
                    enemies[cp_username] = {'games': 0, 'wins': 0}
                enemies[cp_username]['games'] += 1
                if won: enemies[cp_username]['wins'] += 1

    # Calculate percentages for the templates
    def calc_rate(wins, games):
        return f"{int((wins / games) * 100)}%" if games > 0 else "0%"

    overall_win_rate = calc_rate(total_wins, total_games)
    
    for side in side_stats.values():
        side['rate'] = calc_rate(side['wins'], side['games'])
        
    for role in role_stats.values():
        role['rate'] = calc_rate(role['wins'], role['games'])
        
    for tm in teammates.values():
        tm['rate'] = calc_rate(tm['wins'], tm['games'])
        
    for en in enemies.values():
        en['rate'] = calc_rate(en['wins'], en['games'])

    context = {
        'target_user': target_user,
        'total_games': total_games,
        'overall_win_rate': overall_win_rate,
        'side_stats': side_stats,
        'role_stats': role_stats,
        'last_games': last_games,
        'teammates': teammates,
        'enemies': enemies,
    }
    return render(request, 'game/stats.html', context)

