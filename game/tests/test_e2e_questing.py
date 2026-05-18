import os
from channels.testing import ChannelsLiveServerTestCase
from playwright.sync_api import sync_playwright
from django.contrib.auth.models import User
from django.test import override_settings
from game.models import GameRoom, Player

TEST_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_db_questing.sqlite3")
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

@override_settings(
    DATABASES={
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': TEST_DB_PATH,
            # CRITICAL FIX 1: Explicitly tell the test runner to use the physical file
            'TEST': {
                'NAME': TEST_DB_PATH, 
            }
        }
    },
    CHANNEL_LAYERS={
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer'
        }
    }
)
class QuestingE2ETests(ChannelsLiveServerTestCase):
    
    port = 8082 

    @classmethod
    def setUpClass(cls):
        # CRITICAL FIX 2: Remove the connections.close_all() hack.
        # Let ChannelsLiveServerTestCase handle the DB connections naturally.
        super().setUpClass()

    def setUp(self):
        self.users = [User.objects.create_user(username=f"q_player_{i}", password="pw") for i in range(1, 6)]
        self.room = GameRoom.objects.create(room_code="QST5P", host=self.users[0])
        for user in self.users:
            Player.objects.create(user=user, game=self.room)

    def test_questing_phase_success(self):
        """Simulate a team going on a mission and both players playing a Success card."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            contexts = [browser.new_context() for _ in range(5)]
            pages = [context.new_page() for context in contexts]

            # 1. Login & Join
            for i, page in enumerate(pages):
                page.goto(f"{self.live_server_url}/login/")
                page.fill('input[name="username"]', self.users[i].username)
                page.fill('input[name="password"]', "pw")
                page.click('button[type="submit"]')
                page.goto(f"{self.live_server_url}/room/{self.room.room_code}/")
                page.wait_for_load_state("networkidle")

            # 2. Start Game & Propose Team
            pages[0].get_by_role("button", name="Start Game").click()
            
            leader_page = None
            for page in pages:
                try:
                    page.wait_for_selector("text='Choose 2 players for your mission team'", timeout=3000)
                    leader_page = page
                    break
                except Exception:
                    continue

            # ---> FIX 2: Catch if the leader is missing cleanly <---
            self.assertIsNotNone(leader_page, "Could not find the Leader's screen! Check your HTML text.")                

            # Pick the first 2 players for the mission
            player_seats = leader_page.locator(".player-seat")
            player_seats.nth(0).click()
            player_seats.nth(1).click()
            leader_page.get_by_role("button", name="OK").click()

            # 3. Approve Team
            for page in pages:
                btn = page.get_by_role("button", name="Approve")
                btn.wait_for(state="visible", timeout=5000)
                btn.click()

            # 4. THE QUESTING PHASE
            print("[TEST INFO] Team approved. Moving to the Questing Phase...")
            pages[0].wait_for_timeout(1000) # Let the server shift the phase
            
            # Find the browsers of the 2 players actually on the mission
            self.room.refresh_from_db()
            team_member_usernames = [p.user.username for p in self.room.proposed_team.all()]
            
            mission_pages = []
            for i, user in enumerate(self.users):
                if user.username in team_member_usernames:
                    mission_pages.append(pages[i])
                    
            self.assertEqual(len(mission_pages), 2, "There should be exactly 2 players on this mission.")
            
            # 5. Play Success Cards
            for page in mission_pages:
                ok_btn = page.get_by_role("button", name="OK", exact=True)
                ok_btn.wait_for(state="visible", timeout=5000)
                ok_btn.click()                
                success_btn = page.get_by_role("button", name="Success")
                success_btn.wait_for(state="visible", timeout=5000)
                success_btn.click()
                
            # 6. Verify Round Advancement
            pages[0].wait_for_timeout(1000)
            self.room.refresh_from_db()
            
            self.assertEqual(self.room.score_good, 1, "Good's score should have increased by 1.")
            self.assertEqual(self.room.current_round, 2, "Game should have advanced to Round 2.")

            browser.close()
            
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass