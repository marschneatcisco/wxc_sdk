"""
Rooms
Rooms are virtual meeting places where people post messages and collaborate to get work done. This API is used to
manage the rooms themselves. Rooms are created and deleted with this API. You can also update a room to change
its title, for example.

"""

__all__ = ['RoomType', 'RoomMeeting', 'Room', 'RoomsApi']

import datetime

from collections.abc import Generator
from enum import Enum
from pydantic import Field
from typing import Optional

from ..api_child import ApiChild
from ..base import ApiModel, to_camel


class RoomType(str, Enum):
    """
    Room type
    """
    direct = 'direct'
    group = 'group'


class RoomMeeting(ApiModel):
    #: The unique identifier for the room.
    room_id: Optional[str] = Field(alias='id')
    #: The Webex meeting URL for the room.
    meeting_link: Optional[str]
    #: The SIP address for the room.
    sip_address: Optional[str]
    #: The Webex meeting number for the room.
    meeting_number: Optional[str]
    #: The Webex meeting ID for the room
    meeting_id: Optional[str]
    #: The toll-free PSTN number for the room.
    call_in_toll_free_number: Optional[str]
    #: The toll (local) PSTN number for the room.
    call_in_toll_number: Optional[str]


class Room(ApiModel):
    #: The unique identifier for the room.
    room_id: Optional[str] = Field(alias='id')
    #: A user-friendly name for the room.
    title: Optional[str]
    #: The room type (direct or group).
    room_type: Optional[RoomType] = Field(alias='type')
    #: Whether the room is moderated (locked) or not.
    is_locked: Optional[bool]
    #: The ID for the team with which this room is associated.
    team_id: Optional[str]
    #: The date and time of the room's last activity.
    last_activity: Optional[datetime.datetime]
    #: The ID of the person who created this room.
    creator_id: Optional[str]
    #: The date and time the room was created.
    created: Optional[datetime.datetime]
    #: The ID of the organization which owns this room.
    owner_id: Optional[str]
    #: Space classification ID represents the space's current classification. It can be attached during space
    #  creation time, and can be modified at the request of an authorized user.
    classification_id: Optional[str]
    #: Indicates when a space is in Announcement Mode where only moderators can post messages.
    is_announcement_only: Optional[bool]
    #: A compliance officer can set a direct room as read-only, which will disallow any new information exchanges
    #  in this space, while maintaing historical data.
    is_read_only: Optional[bool]


class RoomsApi(ApiChild, base='rooms'):
    def list(self, team_id: str = None, room_type: str = None, **params) -> Generator[Room, None, None]:
        """
        List rooms.

        The title of the room for 1:1 rooms will be the display name of the other person.

        By default, lists rooms to which the authenticated user belongs.

        Long result sets will be split into pages.

        Known Limitations: The underlying database does not support natural sorting by lastactivity and will only
        sort on limited set of results, which are pulled from the database in order of roomId. For users or bots
        in more than 3000 spaces this can result in anomalies such as spaces that have had recent activity not
        being returned in the results when sorting by lastacivity.

        :param team_id: List rooms associated with a team, by ID
        :type team_id: str
        :param room_type: List rooms by type
        :type room_type: str
        :return: yield :class:`Room` instances
        """
        params.update((to_camel(k), v)
                      for i, (k, v) in enumerate(locals().items())
                      if i and v is not None and k != 'params')

        ep = self.ep()
        # noinspection PyTypeChecker
        return self.session.follow_pagination(url=ep, model=Room, params=params)

    def create(self, settings: Room) -> Room:
        """
        Create a Room

        Creates a room. The authenticated user is automatically added as a member of the room. See the Memberships
        API to learn how to add more people to the room.

        To create a 1:1 room, use the Create Messages endpoint to send a message directly to another person by
        using the toPersonId or toPersonEmail parameters.

        Bots are not able to create and classify a room. A bot may update a space classification after a person of
        the same owning organization joined the space as the first human user. A space can only be put into
        announcement mode when it is locked.


        :param settings: settings for new room
        :type settings: Room
        :return: new room
        :rtype: Room
        """
        url = self.ep()
        data = settings.json(exclude={'room_id': True,
                                      'type': True,
                                      'lastActivity': True,
                                      'creatorId': True,
                                      'created': True,
                                      'ownerId': True,
                                      'room_type': True})
        return Room.parse_obj(self.post(url, data=data))

    def details(self, room_id: str) -> Room:
        """
        Get Room Details

        Shows details for a Room, by ID.

        :param room_id: The unique identifier for the Room.
        :type room_id: str
        :return: room details
        :rtype: :class:`Room`
        """
        url = self.ep(room_id)
        data = self.get(url=url)
        return Room.parse_obj(data)

    def meeting(self, room_id: str) -> RoomMeeting:
        """
        Get Room Meeting Details

        Shows Webex meeting details for a room such as the SIP address, meeting URL, toll-free and toll
        dial-in numbers.


        :param room_id: The unique identifier for the Room.
        :type room_id: str
        :return: room meeting details
        :rtype: :class:`RoomMeeting`
        """
        url = self.ep(room_id)
        data = self.get(url=url)
        return Room.parse_obj(data)

    def update(self, room: Room) -> Room:
        """
        Update a Room.

        Updates details for a room, by ID.

        Specify the room ID in the roomId parameter in the URI. A space can only be put into announcement mode when
        it is locked.

        :param room: The room to update
        :type room: Room
        :return: Room details
        :rtype: Room
        """
        if not all(v is not None
                   for v in room.room_id):
            raise ValueError('roomId is required')

        # some attributes should not be included in update
        data = room.json(exclude={'type': True,
                                  'lastActivity': True,
                                  'creatorId': True,
                                  'created': True,
                                  'ownerId': True,
                                  'room_type': True})

        ep = self.ep(path=room.room_id)
        return Room.parse_obj(self.put(url=ep, data=data))

    def delete(self, room_id: str):
        """
        Delete Room

        Deletes a room, by ID. Deleted rooms cannot be recovered. As a security measure to prevent accidental
        deletion, when a non moderator deletes the room they are removed from the room instead.
        Deleting a room that is part of a team will archive the room instead.


        :param room_id: The unique identifier for the organization.
        :type room_id: str
        """
        url = self.ep(room_id)
        super().delete(url=url)
