import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import Spectator

class GameRoomConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_code = self.scope['url_route']['kwargs']['room_code']
        self.room_group_name = f'game_{self.room_code}'

        # Join the room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        # Leave the room group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

        # --- Remove spectator if they disconnect/close tab ---
        user = self.scope.get('user')
        if user and user.is_authenticated:
            # Check if this user was a spectator, and delete them if so
            removed = await self.remove_spectator(user, self.room_code)
            
            if removed:
                # If a spectator was successfully removed, broadcast to the room
                # so all other players' screens instantly update to hide the spectator's name
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'game_update',
                        'event_type': 'spectator_left',
                    }
                )        

    # Helper method to safely interact with the database asynchronously
    @database_sync_to_async
    def remove_spectator(self, user, room_code):
        spectator = Spectator.objects.filter(user=user, game__room_code=room_code).first()
        if spectator:
            spectator.delete()
            return True
        return False                

    # Receive message from room group (triggered by views)
    async def game_update(self, event):
        # Send the ENTIRE event dictionary to the WebSocket browser client
        await self.send(text_data=json.dumps(event))
