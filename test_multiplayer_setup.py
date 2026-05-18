from channels.testing import ChannelsLiveServerTestCase
from playwright.sync_api import sync_playwright
from django.contrib.auth.models import User
import re

class TestAvalonMultiplayer(ChannelsLiveServerTestCase):

    def setUp(self):
        """Create 8 players in the test database before running the test."""
        self.player_credentials = [
            {"username": f"player_{i}", "password": "TestPassword123!"} 
            for i in range(1, 9)
        ]
        
        for cred in self.player_credentials:
            User.objects.create_user(username=cred["username"], password=cred["password"])

    def test_8_players_join_lobby(self):
        """Verify 8 users can log in, join the same room, and the host can see them."""
        with sync_playwright() as p:
            # 1. Launch a single headless browser engine
            browser = p.chromium.launch(headless=True)
            
            # 2. Spin up 8 isolated browser contexts and pages
            contexts = [browser.new_context() for _ in range(8)]
            pages = [context.new_page() for context in contexts]
            
            # 3. Log all 8 players into your Django app concurrently
            for i, page in enumerate(pages):
                cred = self.player_credentials[i]
                page.goto(f"{self.live_server_url}/login/")
                
                # Django's default auth form uses name="username" and name="password"
                page.fill("input[name='username']", cred["username"])
                page.fill("input[name='password']", cred["password"])
                page.click("button[type='submit']")
                
                # Fast verification that they hit the home dashboard
                page.wait_for_url(f"{self.live_server_url}/")

            # 4. Player 1 (The Host) creates a new game room
            # Looking at home.html, your button says "Create Game"
            pages[0].click("button:has-text('Create Game')")
            
            # Wait for the host to be redirected to the room page
            # Example room URL structure: /room/ABCDEF/
            pages[0].wait_for_url(re.compile(r"/room/[A-Z0-9]{6}/"))
            room_url = pages[0].url
            
            # Extract the 6-character room code from the URL for validation
            room_code = room_url.split("/")[-2]
            print(f"\n[TEST INFO] Dynamic Room Created Successfully: {room_code}")

            # 5. Players 2 through 8 navigate directly to that room URL to join
            for page in pages[1:]:
                page.goto(room_url)
                # Give the WebSocket a brief moment to connect and register the player seat
                page.wait_for_load_state("networkidle")

            # 6. Assertion: Check that the host's screen shows all 8 players in the lobby
            # This ensures your WebSocket consumer broadcasted the new seats to the host!
            host_page_content = pages[0].content()
            for cred in self.player_credentials:
                assert cred["username"] in host_page_content, f"{cred['username']} not visible to host!"

            print("[TEST SUCCESS] All 8 players successfully connected to the real-time lobby.")
            browser.close()
