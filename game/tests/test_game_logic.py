from django.test import TestCase
from django.contrib.auth.models import User
from game.models import GameRoom, Player
from game.logic import start_game_and_assign_roles

class RoleAssignmentTests(TestCase):

    def setUp(self):
        """Create 10 mock users to use across our different player-count tests."""
        self.users = [
            User.objects.create_user(username=f"test_player_{i}", password="password123!")
            for i in range(1, 11)
        ]

    def test_8_player_role_assignment(self):
        """Test an 8-player game assigns exactly 5 Good and 3 Evil, with unique special roles."""
        # 1. Setup the Room and 8 Players
        room = GameRoom.objects.create(room_code="ROOM8P", host=self.users[0])
        
        for user in self.users[:8]:  # Slice the first 8 users
            Player.objects.create(user=user, game=room)

        # 2. Trigger the role assignment logic from logic.py
        start_game_and_assign_roles(room)

        # 3. Fetch the updated players from the database
        players = Player.objects.filter(game=room)
        
        # 4. Separate them using your 'is_good' boolean field
        good_players = [p for p in players if p.is_good]
        evil_players = [p for p in players if not p.is_good]

        # 5. Assert the Team Balance (8 Players = 5 Good, 3 Evil)
        self.assertEqual(len(good_players), 5, "There should be exactly 5 Good players.")
        self.assertEqual(len(evil_players), 3, "There should be exactly 3 Evil players.")

        # 6. Assert Special Roles are Unique (Title case!)
        roles = [p.role for p in players]
        self.assertEqual(roles.count('Merlin'), 1, "There must be exactly one Merlin.")
        self.assertEqual(roles.count('Assassin'), 1, "There must be exactly one Assassin.")

    def test_10_player_role_assignment(self):
        """Test a 10-player game assigns exactly 6 Good and 4 Evil."""
        # 1. Setup the Room and all 10 Players
        room = GameRoom.objects.create(room_code="RM10P", host=self.users[0])
        
        for user in self.users:  # Use all 10 users
            Player.objects.create(user=user, game=room)

        # 2. Trigger role assignment
        start_game_and_assign_roles(room)

        # 3. Fetch players
        players = Player.objects.filter(game=room)
        
        good_players = [p for p in players if p.is_good]
        evil_players = [p for p in players if not p.is_good]

        # 4. Assert the Team Balance (10 Players = 6 Good, 4 Evil)
        self.assertEqual(len(good_players), 6, "There should be exactly 6 Good players.")
        self.assertEqual(len(evil_players), 4, "There should be exactly 4 Evil players.")
