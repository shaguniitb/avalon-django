# game/logic.py
import random
import string
from django.utils import timezone
from django.contrib.auth.models import User
from .models import GameRoom, Player, Mission, TeamProposal

# Dictionary defining (Good, Evil) counts based on total players
AVALON_DISTRIBUTION = {
    5: (3, 2),
    6: (4, 2),
    7: (4, 3),
    8: (5, 3),
    9: (6, 3),
    10: (6, 4)
}

# Format: Player Count: [(Team Size, Fails Needed for R1), (R2), (R3), (R4), (R5)]
MISSION_RULES = {
    5: [(2, 1), (3, 1), (2, 1), (3, 1), (3, 1)],
    6: [(2, 1), (3, 1), (4, 1), (3, 1), (4, 1)],
    7: [(2, 1), (3, 1), (3, 1), (4, 2), (4, 1)],
    8: [(3, 1), (4, 1), (4, 1), (5, 2), (5, 1)],
    9: [(3, 1), (4, 1), (4, 1), (5, 2), (5, 1)],
    10: [(3, 1), (4, 1), (4, 1), (5, 2), (5, 1)],
}

def get_required_team_size(num_players, round_number):
    mission_sizes = {
        5:  [2, 3, 2, 3, 3],
        6:  [2, 3, 4, 3, 4],
        7:  [2, 3, 3, 4, 4],
        8:  [3, 4, 4, 5, 5],
        9:  [3, 4, 4, 5, 5],
        10: [3, 4, 4, 5, 5],
    }
    team_sizes = mission_sizes.get(num_players, mission_sizes[5])
    return team_sizes[round_number - 1]

def start_game_and_assign_roles(game_room, special_roles=None):
    if special_roles is None:
        special_roles = []

    players = list(game_room.players.all())
    num_players = len(players)
    
    if num_players not in AVALON_DISTRIBUTION:
        raise ValueError("Avalon requires between 5 and 10 players.")

    num_good, num_evil = AVALON_DISTRIBUTION[num_players]

    good_roles = ['Merlin']
    evil_roles = ['Assassin']

    if 'Percival' in special_roles:
        good_roles.append('Percival')
    if 'Morgana' in special_roles:
        evil_roles.append('Morgana')
    if 'Mordred' in special_roles:
        evil_roles.append('Mordred')
    if 'Oberon' in special_roles:
        evil_roles.append('Oberon')
    if 'Lovers' in special_roles:
        good_roles.extend(['Lover', 'Lover'])

    if len(good_roles) > num_good or len(evil_roles) > num_evil:
        raise ValueError("Too many special roles selected for this player count!")
    
    while len(good_roles) < num_good:
        good_roles.append('Loyal Servant of Arthur')

    while len(evil_roles) < num_evil:
        evil_roles.append('Minion of Mordred')

    roles = good_roles + evil_roles
    random.shuffle(roles)
    random.shuffle(players)
    
    for index, player in enumerate(players):
        player.seat_order = index + 1
        player.role = roles[index]
        player.is_good = player.role in ['Merlin', 'Percival', 'Loyal Servant of Arthur', 'Lover']
        player.save()
        
    game_room.current_phase = 'TEAM_BUILDING'
    game_room.current_leader = players[0] 
    game_room.save()

    return True

def tally_team_votes(game_room, votes):
    proposal = TeamProposal.objects.create(
        game=game_room,
        round_number=game_room.current_round,
        leader=game_room.current_leader,
    )
    proposal.team.set(game_room.proposed_team.all())
    proposal.approves.set(game_room.players.filter(last_vote='Approve'))
    proposal.rejects.set(game_room.players.filter(last_vote='Reject'))

    approves = votes.count(True)
    rejects = votes.count(False)
    
    if approves > rejects:
        game_room.current_phase = 'QUESTING'
        game_room.failed_votes = 0 
    else:
        game_room.failed_votes += 1
        
        if game_room.failed_votes >= 5:
            game_room.current_phase = 'EVIL_WINS' 
            game_room.victory_reason = '5_FAILED_VOTES'
        else:
            current_seat = game_room.current_leader.seat_order
            total_players = game_room.players.count()
            
            next_seat = (current_seat % total_players) + 1 
            next_leader = game_room.players.get(seat_order=next_seat)
            
            game_room.current_leader = next_leader
            game_room.current_phase = 'TEAM_BUILDING'
            game_room.proposed_team.clear()
            
    game_room.save()

def get_player_knowledge(room, player):
    knowledge_text = []
    known_ids = []

    if room.current_phase == 'LOBBY':
        return {"text": [], "ids": []}

    if player.role == 'Merlin':
        evils = room.players.filter(is_good=False).exclude(role='Mordred')
        for e in evils:
            knowledge_text.append(f"{e.user.username} is Evil.")
            known_ids.append(e.id)
            
    elif not player.is_good and player.role != 'Oberon':
        teammates = room.players.filter(is_good=False).exclude(id=player.id).exclude(role='Oberon')
        for t in teammates:
            knowledge_text.append(f"{t.user.username} is a fellow Minion.")
            known_ids.append(t.id)

    elif player.role == 'Percival':
        merlins = room.players.filter(role__in=['Merlin', 'Morgana'])
        for m in merlins:
            knowledge_text.append(f"{m.user.username} is Merlin or Morgana.")
            known_ids.append(m.id)
    
    elif player.role == 'Lover':
        other_lover = room.players.filter(role='Lover').exclude(id=player.id).first()
        if other_lover:
            knowledge_text.append(f"{other_lover.user.username} is your Lover.")
            known_ids.append(other_lover.id)

    return {"text": knowledge_text, "ids": known_ids}

def tally_quest_votes(game_room, votes):
    fails = votes.count(False)
    num_players = game_room.players.count()
    round_index = min(game_room.current_round - 1, 4) 
    
    team_size, required_fails = MISSION_RULES[num_players][round_index]
    did_succeed = (fails < required_fails)

    # ---> THIS IS NOW THE ONLY TALLY FUNCTION. IT PROPERLY SAVES THE MISSION.
    Mission.objects.create(
        game=game_room,
        round_number=game_room.current_round,
        team_size=team_size,
        requires_two_fails=(required_fails > 1),
        did_succeed=did_succeed,
        fails_count=fails
    )

    if did_succeed:
        game_room.score_good += 1
    else:
        game_room.score_evil += 1
        
    # --- NEW: Pause the game right here for the animation! ---
    game_room.current_phase = 'QUEST_REVEAL'
    game_room.save()

def attempt_assassination(game_room, target_player_ids):
    targets = game_room.players.filter(id__in=target_player_ids)

    # 1 target = Attempting to shoot Merlin
    if len(targets) == 1:
        if targets.first().role == 'Merlin':
            game_room.current_phase = 'EVIL_WINS'
            game_room.victory_reason = 'MERLIN_KILLED'
        else:
            game_room.current_phase = 'GOOD_WINS'
            
    # 2 targets = Attempting to shoot the Lovers
    elif len(targets) == 2:
        roles = [t.role for t in targets]
        if roles.count('Lover') == 2:
            game_room.current_phase = 'EVIL_WINS'
            game_room.victory_reason = 'LOVERS_KILLED'
        else:
            game_room.current_phase = 'GOOD_WINS'    
    
    # Invalid data fallback
    else:
        game_room.current_phase = 'GOOD_WINS'
        
    game_room.save()

def transition_to_new_room(old_room):
    """
    Creates a fresh lobby, moves all players to it, and sets the 
    rematch_code on the old room to redirect everyone.
    Returns the new room code.
    """
    # Generate a new unique room code
    while True:
        new_code = ''.join(random.choices(string.ascii_uppercase, k=5))
        if not GameRoom.objects.filter(room_code=new_code).exists():
            break
            
    # Create a completely fresh game room
    new_room = GameRoom.objects.create(
        room_code=new_code,
        host=old_room.host,
        allow_spectator_spoilers=old_room.allow_spectator_spoilers
    )
    
    # Clone all players from the old room as brand new instances in the new room
    for p in old_room.players.all():
        Player.objects.create(user=p.user, game=new_room)
        
    # Set the forwarding address on the old room
    old_room.rematch_code = new_code
    old_room.save()
    
    return new_code

def get_top_players(limit=5):
    """
    Calculates the win rates for all players across completed games
    and returns a sorted list of the top players.
    """
    top_players = []
    
    # Grab all users who have participated in a completed game
    users_with_games = User.objects.filter(
        player__game__current_phase__in=['GOOD_WINS', 'EVIL_WINS']
    ).distinct()
    
    for u in users_with_games:
        # Fetch all completed game records for this specific user
        completed_players = Player.objects.filter(
            user=u, 
            game__current_phase__in=['GOOD_WINS', 'EVIL_WINS']
        ).select_related('game')
        
        games_played = len(completed_players)
        
        if games_played > 0:
            # Tally the wins
            wins = sum(
                1 for p in completed_players 
                if (p.is_good and p.game.current_phase == 'GOOD_WINS') or 
                   (not p.is_good and p.game.current_phase == 'EVIL_WINS')
            )
            
            win_rate = wins / games_played
            
            top_players.append({
                'username': u.username,
                'games': games_played,
                'win_rate_val': win_rate,
                'win_rate_str': f"{int(win_rate * 100)}%"
            })
            
    # Sort by win rate (descending), then by games played (descending) as a tie-breaker
    top_players.sort(key=lambda x: (x['win_rate_val'], x['games']), reverse=True)
    
    # Return only the requested number of top players
    return top_players[:limit]