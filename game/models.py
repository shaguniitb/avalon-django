from django.db import models
from django.contrib.auth.models import User

class GameRoom(models.Model):
    room_code = models.CharField(max_length=6, unique=True)
    is_active = models.BooleanField(default=True)

    # Game State Tracking
    # e.g., 'LOBBY', 'TEAM_BUILDING', 'TEAM_VOTING', 'QUESTING', 'ASSASSINATION', 'FINISHED'
    current_phase = models.CharField(max_length=20, default='LOBBY')
    current_round = models.IntegerField(default=1) # 1 through 5
    failed_votes = models.IntegerField(default=0) # Tracks the 5-vote track

    # Relationships
    host = models.ForeignKey(User, on_delete=models.CASCADE, related_name='hosted_games')
    current_leader = models.ForeignKey('Player', on_delete=models.SET_NULL, null=True)

    proposed_team = models.ManyToManyField('Player', related_name='proposed_for', blank=True)

    score_good = models.IntegerField(default=0)
    score_evil = models.IntegerField(default=0)
    victory_reason = models.CharField(max_length=50, null=True, blank=True)
    assassination_start_time = models.DateTimeField(null=True, blank=True)

    @property
    def hammer_player(self):
        """Calculates and returns the player who holds the hammer for the 5th vote."""
        if self.current_phase == 'LOBBY' or not self.current_leader:
            return None
            
        num_players = self.players.count()
        if num_players == 0:
            return None

        current_seat = self.current_leader.seat_order
        
        # Calculate who will make the 5th proposal (loops around the table)
        hammer_seat = (current_seat + (4 - self.failed_votes) - 1) % num_players + 1
        return self.players.filter(seat_order=hammer_seat).first()

    @property
    def mission_track(self):
        """Builds the 5-square mission track status for the UI."""       
        from .logic import get_required_team_size          
        num_players = self.players.count()
        missions = {m.round_number: m for m in self.missions.all()}
        track = []
        
        for r in range(1, 6):
            size = get_required_team_size(num_players, r) if num_players >= 5 else 0
            status = 'pending'
            
            # Check the database for the results of this round
            if r in missions:
                status = 'success' if missions[r].did_succeed else 'fail'
                
            track.append({
                'round': r,
                'size': size,
                'status': status
            })
            
        return track    

    @property
    def roles_in_play(self):
        """Returns a comma-separated string of roles, sorted by Good -> Evil."""
        roles = [player.role for player in self.players.all() if player.role]
        
        # Get a list of unique roles
        unique_roles = list(set(roles))
        
        # Define the exact sorting order
        sort_order = {
            'Merlin': 1,
            'Percival': 2,
            'Lover': 3,
            'Lovers': 3, # Just in case you named them plural!
            'Loyal Servant of Arthur': 4,
            'Morgana': 5,
            'Mordred': 6,
            'Assassin': 7
        }
        
        # Sort based on the dictionary above. 
        # (The '100' ensures any unexpected/new roles like 'Oberon' just go to the very end)
        unique_roles.sort(key=lambda role: sort_order.get(role, 100))

        evil_roles = ['Morgana', 'Mordred', 'Assassin', 'Minion of Mordred', 'Oberon']
        
        formatted_roles = []
        for role in unique_roles:
            count = roles.count(role)
            display_name = f"{role}({count})" if count > 1 else role

            # Append a dictionary so the HTML knows if it's evil or not
            formatted_roles.append({
                'name': display_name,
                'is_evil': role in evil_roles
            })
        return formatted_roles

class Player(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    game = models.ForeignKey(GameRoom, on_delete=models.CASCADE, related_name='players')
    role = models.CharField(max_length=50, null=True, blank=True) # e.g., 'Merlin', 'Assassin', 'Loyal Servant'
    is_good = models.BooleanField(default=True)
    seat_order = models.IntegerField(null=True) # Helps pass the leader token
    has_voted = models.BooleanField(default=False)
    vote_approve = models.BooleanField(null=True, blank=True)
    has_quest_voted = models.BooleanField(default=False)
    quest_vote_success = models.BooleanField(null=True, blank=True)
    last_vote = models.CharField(max_length=10, null=True, blank=True)

class Mission(models.Model):
    game = models.ForeignKey(GameRoom, on_delete=models.CASCADE, related_name='missions')
    round_number = models.IntegerField()
    team_size = models.IntegerField()
    requires_two_fails = models.BooleanField(default=False) # For Round 4 with 7+ players

    # Outcomes
    is_completed = models.BooleanField(default=False)
    did_succeed = models.BooleanField(null=True)
    fails_count = models.IntegerField(default=0)

class TeamProposal(models.Model):
    game = models.ForeignKey(GameRoom, on_delete=models.CASCADE, related_name='history_proposals')
    round_number = models.IntegerField()
    leader = models.ForeignKey('Player', on_delete=models.SET_NULL, null=True, related_name='history_led')
    team = models.ManyToManyField('Player', related_name='history_team')
    approves = models.ManyToManyField('Player', related_name='history_approves')
    rejects = models.ManyToManyField('Player', related_name='history_rejects')

class Spectator(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    game = models.ForeignKey(
        GameRoom,
        on_delete=models.CASCADE,
        related_name='spectators'
    )

    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'game')    