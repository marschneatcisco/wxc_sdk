"""
Messages
Messages are how you communicate in a room. In Webex, each message is displayed on its own line along with a timestamp
and sender information. Use this API to list, create, update, and delete messages.

Message can contain plain text, rich text, and a file attachment.

Just like in the Webex app, you must be a member of the room in order to target it with this API.
"""

__all__ = ['AdaptiveCardActions', 'AdaptiveCardObject', 'AdaptiveCard', 'Attachment', 'Message', 'MessagesApi']

import datetime

from collections.abc import Generator
from enum import Enum
from pydantic import Field
from typing import Optional

from ..api_child import ApiChild
from ..base import ApiModel, ApiModelWithErrors, to_camel


class RoomType(str, Enum):
    """
    Room type
    """
    direct = 'direct'
    group = 'group'


class AdaptiveCardActions(ApiModel):
    """
    Adaptive card actions.
    """
    #: The card's actions. Possible values: Action.OpenUrl
    type: Optional[str]
    #: Possible values: http://adaptivecards.io
    url: Optional[str]
    #: Possible values: Learn More
    title: Optional[str]


class AdaptiveCardObject(ApiModel):
    """
    Adaptive card object data structure.
    """
    #: The card's elements. Possible values: TextBlock
    type: Optional[str]
    #: Possible values: Adaptive Cards
    text: Optional[str]
    #: Possible values: large
    size: Optional[str]


class AdaptiveCardType(str, Enum):
    """
    Adaptive card type. Currently only AdaptiveCard is supported.
    """
    adaptivecard = 'AdaptiveCard'


class AdaptiveCard(ApiModel):
    #: The room type (direct or group).
    adaptive_card_type: Optional[AdaptiveCardType] = Field(alias='type')
    #: Adaptive Card schema version.
    version: Optional[str]
    #: The card's elements.
    body: Optional[list[AdaptiveCardObject]]
    #: The card's actions.
    actions: Optional[list[AdaptiveCardActions]]


class Attachment(ApiModel):
    """
    An attachment object (Adaptive card).
    """
    #: The content type of the attachment.
    content_type: Optional[str]
    #: Adaptive Card content.
    content: Optional[AdaptiveCard]


class Message(ApiModelWithErrors):
    #: The unique identifier for the room.
    message_id: Optional[str] = Field(alias='id')
    #: The parent message to reply to.
    parent_id: Optional[str]
    #: The room ID of the message.
    room_id: Optional[str]
    #: The room type (direct or group).
    room_type: Optional[RoomType]
    #: The person ID of the recipient when sending a private 1:1 message
    to_person_id: Optional[str]
    #: The email address of the recipient when sending a private 1:1 message
    to_person_email: Optional[str]
    #: The message, in plain text. If markdown is specified this parameter may be optionally used to provide alternate
    #  text for UI clients that do not support rich text.
    text: Optional[str]
    #: The message, in Markdown format.
    markdown: Optional[str]
    #: The text content of the message, in HTML format. This read-only property is used by the Webex clients.
    html: Optional[str]
    #: Public URLs for files attached to the message. For the supported media types and the behavior of file uploads,
    # see Message Attachments.
    files: Optional[list[str]]
    #: The person ID of the message author.
    person_id: Optional[str]
    #: The email address of the message author.
    person_email: Optional[str]
    #: People IDs for anyone mentioned in the message.
    mentioned_people: Optional[list[str]]
    #: Group names for the groups mentioned in the message.
    mentioned_groups: Optional[list[str]]
    #: Message content attachments attached to the message. See the Cards Guide for more information.
    attachments: Optional[list[Attachment]]
    #: The date and time the message was created.
    created: Optional[datetime.datetime]
    #: The date and time that the message was last edited by the author. This field is only present when the message
    #  contents have changed.
    updated: Optional[datetime.datetime]
    #: true if the audio file is a voice clip recorded by the client; false if the audio file is a standard audio
    #  file not posted using the voice clip feature
    is_voice_clip: Optional[bool]


class MessagesApi(ApiChild, base='messages'):
    def list(self, room_id: str = None, parent_id: str = None, mentioned_people: list = [], before: str = None,
             before_message: str = None, **params) -> Generator[Message, None, None]:
        """
        List messages.

        Lists all messages in a room. Each message will include content attachments if present.
        The list sorts the messages in descending order by creation date.
        Long result sets will be split into pages.

        :param room_id: List messages in a room, by ID
        :type room_id: str
        :param parent_id: List messages with a parent, by ID
        :type parent_id: str
        :param mentioned_people: List messages with these people mentioned, by ID. Use me as a shorthand for the
                                 current API user. Only me or the person ID of the current user may be specified.
                                 Bots must include this parameter to list messages in group rooms (spaces)
        :type mentioned_people: list
        :param before: List messages sent before a date and time
        :type before: datetime
        :param before_message: List messages sent before a message, by ID
        :type before_message: str
        :return: yield :class:`Message` instances
        """
        params.update((to_camel(k), v)
                      for i, (k, v) in enumerate(locals().items())
                      if i and v is not None and k != 'params')

        ep = self.ep()
        # noinspection PyTypeChecker
        return self.session.follow_pagination(url=ep, model=Message, params=params)

    def list_direct(self, parent_id: str = None, person_id: str = None, person_email: str = None,
                    **params) -> Generator[Message, None, None]:
        """
        List Direct Messages.

        List all messages in a 1:1 (direct) room. Use the personId or personEmail query parameter to specify the room.
        Each message will include content attachments if present.

        The list sorts the messages in descending order by creation date.

        :param parent_id: List messages with a parent, by ID
        :type parent_id: str
        :param person_id: List messages in a 1:1 room, by person ID.
        :type person_id: str
        :param person_email: List messages in a 1:1 room, by person email
        :type person_email: str
        :return: yield :class:`Message` instances
        """
        params.update((to_camel(k), v)
                      for i, (k, v) in enumerate(locals().items())
                      if i and v is not None and k != 'params')

        ep = self.ep('direct')
        # noinspection PyTypeChecker
        return self.session.follow_pagination(url=ep, model=Message, params=params)

    def create(self, settings: Message) -> Message:
        """
        Create a Message

        Post a plain text or rich text message, and optionally, a file attachment attachment, to a room.

        The files parameter is an array, which accepts multiple values to allow for future expansion, but currently
        only one file may be included with the message. File previews are only rendered for attachments of 1MB or less.


        :param settings: settings for message
        :type settings: Message
        :return: new message
        :rtype: Message
        """
        url = self.ep()
        data = settings.json(exclude={'message_id': True,
                                      'room_type': True,
                                      'person_id': True,
                                      'person_email': True,
                                      'mentioned_people': True,
                                      'mentioned_groups': True,
                                      'created': True,
                                      'updated': True,
                                      'is_voice_clip': True})
        return Message.parse_obj(self.post(url, data=data))

    def details(self, message_id: str) -> Message:
        """
        Get Message Details

        Show details for a message, by message ID.

        :param message_id: The unique identifier for the message.
        :type message_id: str
        :return: message details
        :rtype: :class:`Message`
        """
        url = self.ep(message_id)
        data = self.get(url=url)
        return Message.parse_obj(data)

    def update(self, message: Message) -> Message:
        """
        Edit a Message.

        Update a message you have posted not more than 10 times.
        Specify the messageId of the message you want to edit.

        Edits of messages containing files or attachments are not currently supported. If a user attempts to edit a
        message containing files or attachments a 400 Bad Request will be returned by the API with a message stating
        that the feature is currently unsupported.

        There is also a maximum number of times a user can edit a message. The maximum currently supported is 10 edits
        per message. If a user attempts to edit a message greater that the maximum times allowed the API will return
        400 Bad Request with a message stating the edit limit has been reached.

        While only the roomId and text or markdown attributes are required in the request body, a common pattern
        for editing message is to first call GET /messages/{id} for the message you wish to edit and to then update
        the text or markdown attribute accordingly, passing the updated message object in the request body of the
        PUT /messages/{id} request. When this pattern is used on a message that included markdown, the html attribute
        must be deleted prior to making the PUT request.


        :param message: The room to update
        :type message: Message
        :return: Message details
        :rtype: Message
        """
        if not all(v is not None
                   for v in message.message_id):
            raise ValueError('messageId is required')

        # some attributes should not be included in update
        data = message.json(exclude={'message_id': True,
                                     'room_type': True,
                                     'to_person_id': True,
                                     'to_person_email': True,
                                     'files': True,
                                     'html': True,
                                     'parent_id': True,
                                     'person_email': True,
                                     'mentioned_people': True,
                                     'mentioned_groups': True,
                                     'attachments': True,
                                     'created': True,
                                     'updated': True,
                                     'is_voice_clip': True})

        ep = self.ep(path=message.message_id)
        return Message.parse_obj(self.put(url=ep, data=data))

    def delete(self, message_id: str):
        """
        Delete a Message

        Delete a message, by message ID.

        Specify the message ID in the messageId parameter in the URId.


        :param message_id: The unique identifier for the message.
        :type message_id: str
        """
        url = self.ep(message_id)
        super().delete(url=url)
