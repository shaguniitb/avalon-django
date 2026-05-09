import json
from channels.generic.websocket import AsyncWebsocketConsumer

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

    # Receive message from room group (triggered by views)
    async def game_update(self, event):
        # Send message to the actual WebSocket (the browser)
        await self.send(text_data=json.dumps({
            'type': 'game_update',
            'message': 'Refresh the page!'
        }))