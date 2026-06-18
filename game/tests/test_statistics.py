from django.test import TestCase
from django.contrib.auth.models import User
from game.models import GameRoom, Player, UserProfile
from game.logic import get_top_players

class GameStatisticsTests(TestCase):
    def setUp(self):
        # Create mock users (The post_save signal automatically creates their UserProfiles)
        self.user_alice = User.objects.create_user(username='Alice', password='password')
        self.user_bob = User.objects.create_user(username='Bob', password='password')
        self.user_charlie = User.objects.create_user(username='Charlie', password='password')
        
        # Update the automatically created profiles using display_name
        self.user_alice.profile.display_name = 'Alice Profile'
        self.user_alice.profile.is_test_account = False
        self.user_alice.profile.save()

        self.user_bob.profile.display_name = 'Bob Profile'
        self.user_bob.profile.is_test_account = False
        self.user_bob.profile.save()

        self.user_charlie.profile.display_name = 'Charlie Profile'
        self.user_charlie.profile.is_test_account = False
        self.user_charlie.profile.save()

        # Create a mock completed game where Good wins
        self.game1 = GameRoom.objects.create(
            room_code='GAME1', 
            host=self.user_alice, 
            current_phase='GOOD_WINS'
        )
        
        # Alice is Good (Wins)
        self.player_alice1 = Player.objects.create(user=self.user_alice, game=self.game1, is_good=True, role='Merlin')
        # Bob is Good (Wins)
        self.player_bob1 = Player.objects.create(user=self.user_bob, game=self.game1, is_good=True, role='Percival')
        # Charlie is Evil (Loses)
        self.player_charlie1 = Player.objects.create(user=self.user_charlie, game=self.game1, is_good=False, role='Assassin')

        # Create a second mock completed game where Evil wins
        self.game2 = GameRoom.objects.create(
            room_code='GAME2', 
            host=self.user_alice, 
            current_phase='EVIL_WINS'
        )
        
        # Alice is Good (Loses)
        self.player_alice2 = Player.objects.create(user=self.user_alice, game=self.game2, is_good=True, role='Loyal Servant')
        # Bob is Evil (Wins)
        self.player_bob2 = Player.objects.create(user=self.user_bob, game=self.game2, is_good=False, role='Morgana')

    def test_get_top_players_logic(self):
        """
        Test that get_top_players correctly calculates win rates and net scores across multiple games.
        """
        top_players = get_top_players(limit=5)
        
        # Convert list of dicts to a dictionary keyed by username for easy testing
        stats = {p['username']: p for p in top_players}

        # Alice played 2 games, won 1, lost 1 (50%, Net: 0)
        self.assertEqual(stats['Alice']['games'], 2)
        self.assertEqual(stats['Alice']['win_rate_str'], '50%')
        self.assertEqual(stats['Alice']['net_score'], 0)

        # Bob played 2 games, won 2, lost 0 (100%, Net: 2)
        self.assertEqual(stats['Bob']['games'], 2)
        self.assertEqual(stats['Bob']['win_rate_str'], '100%')
        self.assertEqual(stats['Bob']['net_score'], 2)

        # Charlie played 1 game, won 0, lost 1 (0%, Net: -1)
        self.assertEqual(stats['Charlie']['games'], 1)
        self.assertEqual(stats['Charlie']['win_rate_str'], '0%')
        self.assertEqual(stats['Charlie']['net_score'], -1)

        # Ensure sorting works (Bob should be first due to highest net score/win rate)
        self.assertEqual(top_players[0]['username'], 'Bob')

    def test_player_statistics_view(self):
        """
        Test the detailed player statistics view to ensure teammates, enemies, side stats, and roles are tallied.
        """
        self.client.login(username='Alice', password='password')
        response = self.client.get(f'/stats/{self.user_alice.username}/')
        
        # Check that the page loads successfully
        self.assertEqual(response.status_code, 200)
        
        # Verify core context data
        context = response.context
        self.assertEqual(context['total_games'], 2)
        self.assertEqual(context['overall_win_rate'], '50%')
        
        # Verify Side Stats (Alice played Good twice, won once)
        self.assertEqual(context['side_stats']['Good']['games'], 2)
        self.assertEqual(context['side_stats']['Good']['wins'], 1)
        self.assertEqual(context['side_stats']['Evil']['games'], 0)

        # Verify Role Stats (Merlin = 100% win, Loyal Servant = 0% win)
        self.assertEqual(context['role_stats']['Merlin']['wins'], 1)
        self.assertEqual(context['role_stats']['Loyal Servant']['wins'], 0)

        # Verify Teammates (Alice was Good with Bob in game1, won)
        self.assertIn('Bob', context['teammates'])
        
        # Verify Enemies (Alice was Good vs Charlie in game1, won)
        self.assertIn('Charlie', context['enemies'])