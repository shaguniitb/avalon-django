import os
from channels.testing import ChannelsLiveServerTestCase
from playwright.sync_api import sync_playwright
from django.contrib.auth.models import User
from django.test import override_settings
from game.models import GameRoom, Player

os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

# Ensure we use a physical file for SQLite to prevent thread-locking
TEST_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_db_e2e.sqlite3")

@override_settings(
    DATABASES={
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': TEST_DB_PATH,
            'TEST': {
                'NAME': TEST_DB_PATH,
            }
        }
    }
)
class GameplayE2ETests(ChannelsLiveServerTestCase):
    
    def setUp(self):
        """Set up a 5-player game lobby."""
        self.users = [
            User.objects.create_user(username=f"player_{i}", password="password123!")
            for i in range(1, 6)
        ]
        
        self.room = GameRoom.objects.create(room_code="E2E5P", host=self.users[0])
        
        for user in self.users:
            Player.objects.create(user=user, game=self.room)

    def test_team_proposal_and_voting_flow(self):
        """Simulate 5 browsers logging in, starting the game, proposing, and unanimously approving."""
        
        with sync_playwright() as p:
            # Change headless=False if you want to visually watch the browsers!
            browser = p.chromium.launch(headless=True) 
            
            contexts = [browser.new_context() for _ in range(5)]
            pages = [context.new_page() for context in contexts]

            # 1. Log all 5 players in and navigate to the room
            for i, page in enumerate(pages):
                user = self.users[i]
                page.goto(f"{self.live_server_url}/login/")
                page.fill('input[name="username"]', user.username)
                page.fill('input[name="password"]', "password123!")
                page.click('button[type="submit"]')
                
                # Navigate into the game room
                page.goto(f"{self.live_server_url}/room/{self.room.room_code}/")
                page.wait_for_load_state("networkidle")

            host_page = pages[0]

            # 2. Host starts the game
            print("\n[TEST INFO] Host is starting the game...")
            host_page.get_by_role("button", name="Start Game").click()

            # 3. Find the randomly assigned Leader
            leader_page = None
            for page in pages:
                # Based on your HTML snippet, the leader's screen will tell them to "Choose 2 players"
                try:
                    # Wait up to 3 seconds for the UI to update via WebSockets
                    page.wait_for_selector("text='Choose 2 players for your mission team'", timeout=3000)
                    leader_page = page
                    break
                except Exception:
                    continue
            
            self.assertIsNotNone(leader_page, "Failed to find the Leader's screen after starting the game!")

            # 4. The Leader proposes a team of 2
            print("[TEST INFO] Leader found! Selecting 2 players for the mission...")
            
            # Find the clickable player seats/checkboxes. 
            # Note: If your seat div has a specific class you click to select a player, update '.player-seat' 
            player_seats = leader_page.locator(".player-seat")
            player_seats.nth(0).click()
            player_seats.nth(1).click()
            
            # Submit the proposal
            leader_page.get_by_role("button", name="OK").click()

            # 5. Everyone votes on the team
            print("[TEST INFO] Team proposed. Everyone is voting 'Approve'...")
            for page in pages:
                # Wait for the WebSocket to reveal the Approve button
                approve_btn = page.get_by_role("button", name="Approve")
                approve_btn.wait_for(state="visible", timeout=5000)
                approve_btn.click()

            # 6. Verify the phase advanced to QUESTING
            # Give the server a moment to tally the 5 votes and update the DB
            pages[0].wait_for_timeout(1000) 
            
            self.room.refresh_from_db()
            self.assertEqual(
                self.room.current_phase, 
                "QUESTING", 
                f"Expected phase to be QUESTING, but got {self.room.current_phase}."
            )
            
            print("[TEST SUCCESS] The team was successfully proposed, approved, and moved to the Questing Phase!")

            browser.close()
            
    @classmethod
    def tearDownClass(cls):
        """Clean up the SQLite DB so it doesn't clutter your folder."""
        super().tearDownClass()
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass