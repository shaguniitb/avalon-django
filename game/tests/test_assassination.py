from django.test import TestCase
from django.contrib.auth.models import User
from game.models import GameRoom, Player
from game.logic import attempt_assassination

class AssassinationTests(TestCase):

    def setUp(self):

        self.host_user = User.objects.create_user(username="u_host", password="pw")

        """Set up a room explicitly in the ASSASSINATION phase with our key targets."""
        self.room = GameRoom.objects.create(
            room_code="KILLER", 
            current_phase="ASSASSINATION",
            host=self.host_user
            )
        
        self.merlin_user = User.objects.create_user(username="u_merlin", password="pw")
        self.merlin = Player.objects.create(user=self.merlin_user, game=self.room, role="Merlin", is_good=True)
        
        self.vanilla_user = User.objects.create_user(username="u_vanilla", password="pw")
        self.vanilla = Player.objects.create(user=self.vanilla_user, game=self.room, role="Loyal Servant of Arthur", is_good=True)

        self.lover1_user = User.objects.create_user(username="u_lover1", password="pw")
        self.lover1 = Player.objects.create(user=self.lover1_user, game=self.room, role="Lover", is_good=True)
        
        self.lover2_user = User.objects.create_user(username="u_lover2", password="pw")
        self.lover2 = Player.objects.create(user=self.lover2_user, game=self.room, role="Lover", is_good=True)

    def test_assassin_shoots_merlin(self):
        """Shooting Merlin results in an Evil Win."""
        attempt_assassination(self.room, [self.merlin.id])
        self.room.refresh_from_db()
        
        self.assertEqual(self.room.current_phase, "EVIL_WINS")
        self.assertEqual(self.room.victory_reason, "MERLIN_KILLED")

    def test_assassin_misses_merlin(self):
        """Shooting a vanilla Good member results in a Good Win."""
        attempt_assassination(self.room, [self.vanilla.id])
        self.room.refresh_from_db()
        
        self.assertEqual(self.room.current_phase, "GOOD_WINS")

    def test_assassin_shoots_lovers(self):
        """Shooting exactly both Lovers results in an Evil Win."""
        attempt_assassination(self.room, [self.lover1.id, self.lover2.id])
        self.room.refresh_from_db()
        
        self.assertEqual(self.room.current_phase, "EVIL_WINS")
        self.assertEqual(self.room.victory_reason, "LOVERS_KILLED")
        
    def test_assassin_misses_lovers(self):
        """Shooting one Lover and one Vanilla player results in a Good Win."""
        attempt_assassination(self.room, [self.lover1.id, self.vanilla.id])
        self.room.refresh_from_db()
        
        self.assertEqual(self.room.current_phase, "GOOD_WINS")
