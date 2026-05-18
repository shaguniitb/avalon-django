from django.test import TestCase, Client
from django.contrib.auth.models import User
from game.models import GameRoom, Player
from game.logic import start_game_and_assign_roles

class SecretInformationLeakTests(TestCase):

    def setUp(self):
        """Set up a 10-player game with ALL special roles assigned."""
        self.users = [
            User.objects.create_user(username=f"test_player_{i}", password="password123!")
            for i in range(1, 11)
        ]
        
        self.room = GameRoom.objects.create(room_code="TEST10", host=self.users[0])
        
        for user in self.users:
            Player.objects.create(user=user, game=self.room)

        # Inject all special roles into the setup logic
        special_roles = ['Percival', 'Morgana', 'Mordred', 'Oberon', 'Lovers']
        start_game_and_assign_roles(self.room, special_roles=special_roles)

        # Retrieve the newly assigned players
        self.players = Player.objects.filter(game=self.room)
        
        # Identify the players who received specific roles
        self.merlin = self.players.get(role='Merlin')
        self.percival = self.players.get(role='Percival')
        self.morgana = self.players.get(role='Morgana')
        self.mordred = self.players.get(role='Mordred')
        self.assassin = self.players.get(role='Assassin')
        self.oberon = self.players.get(role='Oberon')
        self.lovers = list(self.players.filter(role='Lover'))
        self.vanilla_good = self.players.filter(role='Loyal Servant of Arthur').first()

    def test_vanilla_good_cannot_see_anyone(self):
        """A regular Good player's page context should completely lack secret IDs."""
        client = Client()
        client.login(username=self.vanilla_good.user.username, password="password123!")
        
        response = client.get(f'/room/{self.room.room_code}/')
        known_ids = response.context['known_player_ids']
        
        self.assertEqual(len(known_ids), 0, "Security Leak: Vanilla Good player received secret IDs!")

    def test_merlin_sees_correct_evil(self):
        """Merlin must see Morgana, Assassin, and Oberon, but NOT Mordred."""
        client = Client()
        client.login(username=self.merlin.user.username, password="password123!")
        
        response = client.get(f'/room/{self.room.room_code}/')
        known_ids = response.context['known_player_ids']
        
        self.assertIn(self.morgana.id, known_ids, "Bug: Merlin cannot see Morgana!")
        self.assertIn(self.assassin.id, known_ids, "Bug: Merlin cannot see the Assassin!")
        self.assertIn(self.oberon.id, known_ids, "Bug: Merlin cannot see Oberon!")
        self.assertNotIn(self.mordred.id, known_ids, "Security Leak: Merlin can see Mordred!")
        
        self.assertEqual(len(known_ids), 3, "Bug: Merlin should see exactly 3 Evil players in this setup.")

    def test_percival_sees_merlin_and_morgana(self):
        """Percival must see both Merlin and Morgana."""
        client = Client()
        client.login(username=self.percival.user.username, password="password123!")
        
        response = client.get(f'/room/{self.room.room_code}/')
        known_ids = response.context['known_player_ids']
        
        self.assertIn(self.merlin.id, known_ids, "Bug: Percival cannot see Merlin!")
        self.assertIn(self.morgana.id, known_ids, "Bug: Percival cannot see Morgana!")
        
        self.assertEqual(len(known_ids), 2, "Bug: Percival should see exactly 2 players.")

    def test_evil_sees_other_evil_except_oberon(self):
        """Regular Evil players (e.g. Assassin) see each other, but not Oberon."""
        client = Client()
        client.login(username=self.assassin.user.username, password="password123!")
        
        response = client.get(f'/room/{self.room.room_code}/')
        known_ids = response.context['known_player_ids']
        
        self.assertIn(self.morgana.id, known_ids, "Bug: Assassin cannot see Morgana!")
        self.assertIn(self.mordred.id, known_ids, "Bug: Assassin cannot see Mordred!")
        self.assertNotIn(self.oberon.id, known_ids, "Security Leak: Assassin can see Oberon!")
        
        self.assertEqual(len(known_ids), 2, "Bug: The Assassin should see exactly 2 fellow evil teammates here.")

    def test_oberon_is_blind(self):
        """Oberon is Evil, but is completely blind to his teammates."""
        client = Client()
        client.login(username=self.oberon.user.username, password="password123!")
        
        response = client.get(f'/room/{self.room.room_code}/')
        known_ids = response.context['known_player_ids']
        
        self.assertEqual(len(known_ids), 0, "Security Leak: Oberon received secret teammate IDs!")

    def test_lovers_see_each_other(self):
        """A Lover's page context must correctly identify their companion Lover."""
        client = Client()
        
        lover_one = self.lovers[0]
        lover_two = self.lovers[1]
        
        client.login(username=lover_one.user.username, password="password123!")
        response = client.get(f'/room/{self.room.room_code}/')
        
        known_ids = response.context['known_player_ids']
        knowledge_text = response.context['knowledge']
        
        self.assertIn(lover_two.id, known_ids, "Security Flaw: Lover One cannot see Lover Two's ID data.")
        expected_text = f"{lover_two.user.username} is your Lover."
        self.assertIn(expected_text, knowledge_text, f"UI Bug: Knowledge text did not print: '{expected_text}'")

    def test_spectator_spoiler_security(self):
        """If spectator spoilers are disabled, spectators should not receive role data."""
        self.room.allow_spectator_spoilers = False
        self.room.save()
        
        spectator_user = User.objects.create_user(username="nosy_spectator", password="password123!")
        
        client = Client()
        client.login(username=spectator_user.username, password="password123!")
        
        client.post('/', {'action': 'spectate', 'room_code': self.room.room_code})
        response = client.get(f'/room/{self.room.room_code}/')
        
        self.assertIsNone(
            response.context.get('spectator_spoilers'), 
            "Security Leak: Spectator received spoiler dictionary when allow_spectator_spoilers is False!"
        )