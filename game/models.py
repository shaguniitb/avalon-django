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

class Player(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    game = models.ForeignKey(GameRoom, on_delete=models.CASCADE, related_name='players')
    role = models.CharField(max_length=20, null=True, blank=True) # e.g., 'Merlin', 'Assassin', 'Loyal Servant'
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