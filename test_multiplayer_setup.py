from channels.testing import ChannelsLiveServerTestCase
from playwright.sync_api import sync_playwright
from django.contrib.auth.models import User
from django.test import override_settings
from game.models import GameRoom, Player
import os

TEST_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_db.sqlite3")

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
class TestAvalonMultiplayer(ChannelsLiveServerTestCase):

    def setUp(self):
        """Create 8 players in the test database before running the test."""
        self.player_credentials = [
            {"username": f"player_{i}", "password": "TestPassword123!"} 
            for i in range(1, 9)
        ]
        
        self.users = []
        for cred in self.player_credentials:
            user = User.objects.create_user(username=cred["username"], password=cred["password"])
            self.users.append(user)

    @classmethod
    def tearDownClass(cls):
        """Clean up the physical test database file after tests complete."""
        super().tearDownClass()
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
                print("\n[TEST INFO] Temporary test database cleared successfully.")
            except OSError:
                pass

    def test_8_players_join_lobby(self):
        """Verify 8 users can log in, join the same room, and the host can see them."""
        
        # 1. Pre-create the room programmatically using Django's ORM to prevent SQLite lockups
        room_code = "TEST8P"
        room = GameRoom.objects.create(
            room_code=room_code,
            host=self.users[0],  # Player 1 is the host
            current_phase="LOBBY"
        )
        print(f"\n[TEST INFO] Dynamic Room Created Programmatically: {room_code}")

        for user in self.users:
            Player.objects.create(user=user, game=room)

        with sync_playwright() as p:
            # 2. Launch a single headless browser engine
            browser = p.chromium.launch(headless=True)
            
            # ... (the rest of the test remains exactly the same!)

        with sync_playwright() as p:
            # 2. Launch a single headless browser engine
            browser = p.chromium.launch(headless=True)
            
            # 3. Spin up 8 isolated browser contexts and pages
            contexts = [browser.new_context() for _ in range(8)]
            pages = [context.new_page() for context in contexts]
            
            # 4. Log all 8 players into your Django app sequentially
            for i, page in enumerate(pages):
                cred = self.player_credentials[i]
                page.goto(f"{self.live_server_url}/login/")
                
                page.fill("input[name='username']", cred["username"])
                page.fill("input[name='password']", cred["password"])
                page.click("button[type='submit']")
                
                # Verify successful dashboard navigation
                page.wait_for_url(f"{self.live_server_url}/")

            # 5. Direct ALL 8 players directly to the pre-built room URL
            room_url = f"{self.live_server_url}/room/{room_code}/"
            
            print("[TEST INFO] Orchestrating 8-player room navigation...")
            for page in pages:
                page.goto(room_url)
                # Ensure the page loads completely and establishes WebSocket handshakes
                page.wait_for_load_state("networkidle")

            # 6. Assertion: Verify the Host's screen registers all 8 connected usernames
            # Give the WebSockets an extra half-second to complete room registration updates
            pages[0].wait_for_timeout(500)
            
            host_page_content = pages[0].content()
            for cred in self.player_credentials:
                assert cred["username"] in host_page_content, f"Error: {cred['username']} not found on host screen!"

            print("[TEST SUCCESS] All 8 players successfully connected to the real-time lobby.")
            browser.close()
