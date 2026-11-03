import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import Spectator, Player, GameRoom, ChatMessage
from django.utils.timezone import localtime

class GameRoomConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_code = self.scope['url_route']['kwargs']['room_code']
        self.user = self.scope['user']
        
        if not self.user.is_authenticated:
            await self.close()
            return        
        
        self.room_group_name = f'game_{self.room_code}'
        self.chat_players_group = f'chat_players_{self.room_code}'
        self.chat_spectators_group = f'chat_spectators_{self.room_code}'      

        # Verify role securely from the database
        self.role = await self.get_user_role(self.user, self.room_code) 

        if self.role == 'player':
            await self.channel_layer.group_add(self.chat_players_group, self.channel_name)
        elif self.role == 'spectator':
            await self.channel_layer.group_add(self.chat_spectators_group, self.channel_name)
        else:
            await self.close()
            return                 

        # Everyone gets main game updates
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
        if getattr(self, 'role', None) == 'player':
            await self.channel_layer.group_discard(self.chat_players_group, self.channel_name)
        elif getattr(self, 'role', None) == 'spectator':
            await self.channel_layer.group_discard(self.chat_spectators_group, self.channel_name)        

    @database_sync_to_async
    def get_sender_name(self):
        return self.user.profile.name

    async def receive(self, text_data):
        data = json.loads(text_data)
        action = data.get('action')

        if action == 'chat_message':
            message = data.get('message')
            if message and message.strip():
                is_spectator_only = (self.role == 'spectator')

                # Save to database asynchronously
                chat_msg = await self.save_message(self.user, self.room_code, message, is_spectator_only)

                # Fetch the name asynchronously FIRST
                sender_name = await self.get_sender_name()

                payload = {
                    'type': 'chat_message_broadcast',
                    'sender_name': sender_name,  # Use the fetched variable here
                    'message': message,
                    'is_spectator_only': is_spectator_only,
                    'timestamp': localtime(chat_msg.timestamp).strftime('%H:%M')
                }

                # Players broadcast to both groups; Spectators only to spectators
                if self.role == 'player':
                    await self.channel_layer.group_send(self.chat_players_group, payload)
                    await self.channel_layer.group_send(self.chat_spectators_group, payload)
                elif self.role == 'spectator':
                    await self.channel_layer.group_send(self.chat_spectators_group, payload)


    async def chat_message_broadcast(self, event):
        await self.send(text_data=json.dumps(event))

    async def game_update(self, event):
        await self.send(text_data=json.dumps(event))      

    @database_sync_to_async
    def get_user_role(self, user, room_code):
        if Player.objects.filter(user=user, game__room_code=room_code).exists():
            return 'player'
        if Spectator.objects.filter(user=user, game__room_code=room_code).exists():
            return 'spectator'
        return None

    @database_sync_to_async
    def save_message(self, user, room_code, message, is_spectator_only):
        game = GameRoom.objects.get(room_code=room_code)
        return ChatMessage.objects.create(
            game=game,
            sender=user,
            message=message,
            is_spectator_only=is_spectator_only
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
