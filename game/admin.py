from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from .models import GameRoom, Player, Mission

# 1. Create an Inline so players appear directly inside the GameRoom detail page
class PlayerInline(admin.TabularInline):
    model = Player
    extra = 0
    # Add a custom read-only field for the direct admin link
    readonly_fields = ('player_admin_link',)
    # Adjust these fields to match whatever attributes your Player model tracks
    fields = ('player_admin_link', 'user', 'role', 'is_good') 

    def player_admin_link(self, obj):
        if obj.id:
            # Dynamically resolves the admin URL pattern regardless of your app's name
            url = reverse(f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change", args=[obj.id])
            return format_html('<a href="{}">View Full Profile ↗️</a>', url)
        return "-"
    player_admin_link.short_description = "Full Admin Link"


@admin.register(GameRoom)
class GameRoomAdmin(admin.ModelAdmin):
    # 2. Display the player links as a column in the main GameRoom table list
    list_display = ('room_code', 'host', 'current_phase', 'get_player_links')
    
    # 3. Include the inline on the GameRoom change/edit form page
    inlines = [PlayerInline]
    
    # 4. Also make the link list available as a read-only section on the room form page
    readonly_fields = ('get_player_links',)

    def get_player_links(self, obj):
        # Query all players assigned to this specific game room
        players = Player.objects.filter(game=obj)
        links = []
        
        for player in players:
            # Generate the specific admin change URL for each player instance
            url = reverse(f"admin:{player._meta.app_label}_{player._meta.model_name}_change", args=[player.id])
            links.append(format_html('<a href="{}">{}</a>', url, player.user.username))
        
        # Join them together into safe HTML links separated by commas
        return format_html(", ".join(links)) if links else "No players registered"
    
    get_player_links.short_description = "Players in Room"


# Register your remaining models normally
admin.site.register(Player)
admin.site.register(Mission)