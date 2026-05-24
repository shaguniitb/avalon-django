from django.test import TestCase
from django.contrib.auth.models import User
from game.models import GameRoom, Player
from game.logic import get_top_players

class LadyOfTheLakeTests(TestCase):
    def setUp(self):
        self.user_alice = User.objects.create_user(username='Alice', password='password')
        self.user_bob = User.objects.create_user(username='Bob', password='password')
        
        self.game = GameRoom.objects.create(
            room_code='LADY1', 
            host=self.user_alice, 
            use_lady_of_the_lake=True,
            current_phase='LADY_PHASE'
        )
        
        self.player_alice = Player.objects.create(user=self.user_alice, game=self.game, is_good=True, role='Merlin')
        self.player_bob = Player.objects.create(user=self.user_bob, game=self.game, is_good=False, role='Assassin')
        
        # Alice starts with the Lady token
        self.game.current_lady_holder = self.player_alice
        self.game.save()

    def test_lady_chain_recording(self):
        """
        Test that passing the Lady of the Lake records the target correctly in the model chain.
        """
        # Alice uses the Lady on Bob
        self.player_alice.lady_target = self.player_bob
        self.player_alice.save()
        
        # The token physically moves to Bob
        self.game.current_lady_holder = self.player_bob
        self.game.save()
        
        # Verify the chain: Bob's 'lady_received_from' reverse lookup should show Alice
        self.assertEqual(self.player_bob.lady_received_from.first(), self.player_alice)
        
        # Verify Alice is no longer the holder
        self.assertEqual(self.game.current_lady_holder, self.player_bob)