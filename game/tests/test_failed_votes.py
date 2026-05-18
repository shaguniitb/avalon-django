from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from game.models import GameRoom, Player, TeamProposal

class FailedVotesTests(TestCase):

    def setUp(self):
        self.users = [User.objects.create_user(username=f"fv_player_{i}", password="pw") for i in range(1, 6)]
        self.room = GameRoom.objects.create(
            room_code="FAIL5P", 
            host=self.users[0], 
            current_phase="TEAM_VOTING",
            failed_votes=4 # Fast-forward the game to the brink of disaster
        )
        for user in self.users:
            Player.objects.create(user=user, game=self.room)

        # Create a dummy Team Proposal so the view has something to attach the votes to!
        self.proposal = TeamProposal.objects.create(
            game=self.room,
            round_number=self.room.current_round,
            leader=self.room.players.first()
        )
            
        self.client = Client()

    def test_fifth_failed_vote_triggers_evil_win(self):
        """Rejecting a team when failed_votes is 4 should immediately end the game."""

        vote_url = reverse('cast_vote', args=[self.room.room_code])
        
        # Simulate all 5 players voting "Reject" via the view endpoint
        for user in self.users:
            self.client.login(username=user.username, password="pw")

            # Submitting the POST data that matches your HTML form
            self.client.post(vote_url, {
                'team_vote': 'reject' 
            })

            
        self.room.refresh_from_db()
        
        # Assert the apocalypse occurred
        self.assertEqual(self.room.current_phase, "EVIL_WINS", "Game did not end on the 5th failed vote!")
        # Adjust '5_FAILED_VOTES' to whatever string you use in your logic.py
        self.assertEqual(self.room.victory_reason, "5_FAILED_VOTES")
