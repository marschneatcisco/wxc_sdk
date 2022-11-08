#!/usr/bin/env python
"""
Example script
Creates a room, sends, retrieves, updates some messages, then updates, counts and deletes the room.
Then gets a total count of the number of rooms.
Pauses are inserted between operations in order to view results in Webex App
"""
from dotenv import load_dotenv

from wxc_sdk import WebexSimpleApi
from wxc_sdk.rooms import Room
from wxc_sdk.messages import Message
from time import sleep

load_dotenv()

delay = 5  # time (in seconds) to wait between operations to observe what is happening in the Webex App
new_room_title = 'EraseMe'

api = WebexSimpleApi()

# Create a new room
settings = Room(title=new_room_title, room_type='group')
new_room = api.rooms.create(settings=settings)
print(f'*** CREATED ROOM "{new_room.title}" with id {new_room.room_id} at {new_room.created}" ***')

# Send a simple message with some text
msg_settings = Message(room_id=new_room.room_id, text="Here is a simple text message #1")
message1 = api.messages.create(settings=msg_settings)
print(f'*** SENT MESSAGE 1 ***\n    Details: {message1}')
sleep(delay)

# Send another message with included link to image file
files = ["https://developer.webex.com/static/images/hero-illustration.9a1679acd6ec6532b6adbc22d9b67e1a.png"]
message2 = api.messages.create(Message(room_id=new_room.room_id, text='Here is message #2 with an image', files=files))
print(f'*** SENT MESSAGE 2 ***\n    Details: {message2}')
sleep(delay)

# Get message details for message 1
message1_det = api.messages.details(message1.message_id)
print(f'*** RETRIEVED MESSAGE DETAILS FOR MESSAGE 1 ***\n    Details: {message1_det}')

# Edit a message
markdown = "**MESSAGE 1 EDITED** Changed from text to markdown with sample link [on Box](http://box.com/s/lf5vj) and " \
           "sample person link, <@personEmail:user@example.com>."
message1_det.markdown = markdown
message1_upd = api.messages.update(message1_det)
print(f'*** EDITED MESSAGE 1 ***\n    Details: {message1_upd}')
sleep(delay)

# Simple Adaptive Card document
attachments = [
    {
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": {
            "type": "AdaptiveCard",
            "version": "1.0",
            "body": [
                {
                    "type": "TextBlock",
                    "text": "Sample Adaptive Card",
                    "size": "large"
                }
            ],
            "actions": [
                {
                    "type": "Action.OpenUrl",
                    "url": "http://adaptivecards.io",
                    "title": "Learn More"
                }
            ]
        }
    }
]

# Send message with Adaptive Card
message3 = api.messages.create(Message(room_id=new_room.room_id, text='test', attachments=attachments))
print(f'*** SENT MESSAGE 3***\n    Details: {message3}')
sleep(delay)

# Delete message #2
api.messages.delete(message2.message_id)

# Get Room details
new_room_det = api.rooms.details(new_room.room_id)
print(f'*** FOUND ROOM "{new_room_title}" ***\n    Details: {new_room_det}')

# Update the room title (append "-Updated" to the title)
new_room_det.title = new_room_title + "-Updated"
updated_room = api.rooms.update(new_room_det)
print(f'*** UPDATED ROOM "{new_room_det.title}" to "{updated_room.title}"***')
sleep(delay)

# Delete the room
api.rooms.delete(updated_room.room_id)
print(f'*** DELETED ROOM "{updated_room.title} " ***\n    Details: {updated_room}')

# Query all rooms to get a count; this step might take a bit
print(f'*** QUERYING ALL ROOMS ***')
room_count = 0
for room in api.rooms.list():
    room_count += 1
print(f'*** FOUND A TOTAL OF {room_count} ROOMS ***')
