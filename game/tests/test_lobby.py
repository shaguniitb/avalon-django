from channels.testing import ChannelsLiveServerTestCase
from playwright.sync_api import sync_playwright

class TestAvalonLobby(ChannelsLiveServerTestCase):
    
    def test_login_page_loads(self):
        """Verify that the Django server spins up and Playwright can read the login page."""
        with sync_playwright() as p:
            # Launch a headless browser session
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # self.live_server_url is dynamically provided by ChannelsLiveServerTestCase
            page.goto(f"{self.live_server_url}/login/")
            
            # Verify the HTML title matches your login.html template
            assert "Login - Avalon" in page.title()
            
            browser.close()
