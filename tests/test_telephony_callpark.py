import json
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from itertools import chain, groupby
from typing import ClassVar, Optional
from unittest import skip

from wxc_sdk.all_types import *
from .base import TestWithLocations

# Number of call parks to create by create many test
CP_MANY = 100


class TestRead(TestWithLocations):
    """
    Test cases for list(), details()
    """

    def test_001_list_all_locations(self):
        """
        List call parks in all locations
        """
        with ThreadPoolExecutor() as pool:
            lists = list(pool.map(
                lambda location: list(self.api.telephony.callpark.list(location_id=location.location_id)),
                self.locations))
            call_parks = list(chain.from_iterable(lists))
        print(f'Got {len(call_parks)} call parks.')

    def test_002_list_by_name(self):
        """
        List call parks by name
        """
        with ThreadPoolExecutor() as pool:
            lists = list(pool.map(
                lambda location: list(self.api.telephony.callpark.list(location_id=location.location_id)),
                self.locations))
            call_parks = list(chain.from_iterable(lists))
        # find a location with multiple call parks and then check that list by name only returns a subset
        by_location = {location_id: callparks
                       for location_id, cpi in groupby(sorted(call_parks, key=lambda cp: cp.location_id),
                                                       key=lambda cp: cp.location_id)
                       if len(callparks := list(cpi)) > 1}
        by_location: dict[str, list[CallPark]]
        if not by_location:
            self.skipTest('Need at least one location with multipla call parks')
        target_location_id = random.choice(list(by_location))
        parks = by_location[target_location_id]
        cq_list = list(self.api.telephony.callpark.list(location_id=target_location_id,
                                                        name=parks[0].name))
        matching_parks = [cp for cp in parks
                          if cp.name.startswith(parks[0].name)]
        self.assertEqual(len(matching_parks), len(cq_list))

    def test_003_all_details(self):
        """
        Get details for all call parks
        """
        with ThreadPoolExecutor() as pool:
            lists = list(pool.map(
                lambda location: list(self.api.telephony.callpark.list(location_id=location.location_id)),
                self.locations))
            call_parks = list(chain.from_iterable(lists))
            if not call_parks:
                self.skipTest('No existing call parks.')
            details = list(pool.map(
                lambda cp: self.api.telephony.callpark.details(location_id=cp.location_id,
                                                               callpark_id=cp.callpark_id),
                call_parks))
        print(f'Got details for {len(details)} call parks.')


class TestCreate(TestWithLocations):
    """
    call park creation
    """

    def test_001_trivial(self):
        """
        create the most trivial call park
        """
        # pick random location
        target_location = random.choice(self.locations)
        print(f'Target location: {target_location.name}')

        # get available call park name in location
        cpa = self.api.telephony.callpark
        call_parks = list(cpa.list(location_id=target_location.location_id, name='cp_'))
        names = set(cp.name for cp in call_parks)
        new_names = (name for i in range(1000) if (name := f'cp_{i:03}') not in names)
        new_name = next(new_names)
        # create call park
        settings = CallPark.default(name=new_name)
        new_id = cpa.create(location_id=target_location.location_id, settings=settings)
        print(f'new call park id: {new_id}')

        details = cpa.details(location_id=target_location.location_id, callpark_id=new_id)
        print('New call park')
        print(json.dumps(json.loads(details.json()), indent=2))

    def test_002_many(self):
        """
        create many call parks and test pagination
        """
        # pick a random location
        target_location = random.choice(self.locations)
        print(f'Target location: {target_location.name}')

        tcp = self.api.telephony.callpark

        # Get names for new call parks
        parks = list(tcp.list(location_id=target_location.location_id))
        print(f'{len(parks)} existing call parks')
        park_names = set(park.name for park in parks)
        new_names = (name for i in range(1000)
                     if (name := f'many_{i:03}') not in park_names)
        names = [name for name, _ in zip(new_names, range(CP_MANY))]
        print(f'got {len(names)} new names')

        def new_park(*, park_name: str):
            """
            Create a new call park with the given name
            :param park_name:
            :return:
            """
            settings = CallPark.default(name=park_name)
            # creat new call park
            new_park_id = tcp.create(location_id=target_location.location_id, settings=settings)
            print(f'Created {park_name}')
            return new_park_id

        with ThreadPoolExecutor() as pool:
            new_parks = list(pool.map(lambda name: new_park(park_name=name),
                                      names))
        print(f'Created {len(new_parks)} call parks.')
        parks = list(self.api.telephony.callpark.list(location_id=target_location.location_id))
        print(f'Total number of call parks: {len(parks)}')
        parks_pag = list(self.api.telephony.callpark.list(location_id=target_location.location_id, max=50))
        print(f'Total number of call parks read with pagination: {len(parks_pag)}')
        self.assertEqual(len(parks), len(parks_pag))


@dataclass(init=False)
class TestUpdate(TestWithLocations):
    """
    Test call park updates
    """
    cp_list: ClassVar[list[CallPark]]
    cp_by_location: ClassVar[dict[str, list[CallPark]]]
    target: CallPark = field(default=None)

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with ThreadPoolExecutor() as pool:
            cp_lists = list(pool.map(
                lambda location: cls.api.telephony.callpark.list(location_id=location.location_id),
                cls.locations))
        cls.cp_list = list(chain.from_iterable(cp_lists))
        cls.cp_by_location = {location_id: cp_list
                              for location_id, cpi in groupby(cls.cp_list,
                                                              key=lambda cp: cp.location_id)
                              if (cp_list := list(cpi))}

    def setUp(self) -> None:
        """
        chose a random call park target and save settings
        """
        super().setUp()
        if not self.cp_list:
            self.skipTest('No existing call parks to mess with')
        target = random.choice(self.cp_list)
        print(f'target call park: "{target.name}" in "{target.location_name}"')
        self.target = self.api.telephony.callpark.details(location_id=target.location_id,
                                                          callpark_id=target.callpark_id)
        # also set location id and name (not returned by details())
        self.target.location_id = target.location_id
        self.target.location_name = target.location_name

    def tearDown(self) -> None:
        """
        Restore settings to original after test
        :return:
        """
        try:
            if self.target:
                print('tearDown: restore settings')
                self.api.telephony.callpark.update(location_id=self.target.location_id,
                                                   callpark_id=self.target.callpark_id,
                                                   settings=self.target)
        finally:
            super().tearDown()

    def test_001_name(self):
        """
        change name
        """
        names = set(cp.name for cp in self.cp_list
                    if cp.location_id == self.target.location_id)
        new_name = next(name
                        for i in range(1000)
                        if (name := f'cp_{i:03}') not in names)
        settings = CallPark(name=new_name)
        cpa = self.api.telephony.callpark
        new_id = cpa.update(location_id=self.target.location_id,
                            callpark_id=self.target.callpark_id,
                            settings=settings)
        self.target.callpark_id = new_id
        details = cpa.details(location_id=self.target.location_id,
                              callpark_id=new_id)
        self.assertEqual(new_name, details.name)
        # .. other than that the updated details should be identical
        details.location_id = self.target.location_id
        details.location_name = self.target.location_name
        details.name = self.target.name
        self.assertEqual(self.target, details)

    def test_002_recall_hg_id(self):
        """
        change recall hunt group id
        """
        # we are not actually using the target chosen by setuo()
        self.target = None
        cpa = self.api.telephony.callpark
        with ThreadPoolExecutor() as pool:
            recall_lists = list(pool.map(
                lambda location: list(cpa.available_recalls(location_id=location.location_id)),
                self.locations))
        # look for locations with existing call parks and available recall hunt groups
        location_candidates = {location.location_id: recall_list for location, recall_list in
                               zip(self.locations, recall_lists)
                               if recall_list and location.location_id in self.cp_by_location}
        location_candidates: dict[str, list[AvailableRecallHuntGroup]]
        if not location_candidates:
            self.skipTest('No location with call parks and available recall hunt groups')
        location_id = random.choice(list(location_candidates))
        recall_list = location_candidates[location_id]
        target = random.choice(self.cp_by_location[location_id])
        print(f'Target call park: "{target.name}" in "{target.location_name}"')
        target_details = cpa.details(location_id=target.location_id,
                                     callpark_id=target.callpark_id)
        target_details.location_id = target.location_id
        target_details.location_name = target.location_name
        recall_hunt_group_id = target_details.recall.hunt_group_id
        if not recall_hunt_group_id:
            new_recall = random.choice(recall_list)
            print(f'Changing recall from None to {new_recall.name}')
            new_recall_id = new_recall.huntgroup_id
            new_recall_name = new_recall.name
        else:
            print(f'Changing recall from {target_details.recall.hunt_group_name} to None')
            new_recall_id = ''
            new_recall_name = None
        settings = CallPark(recall=RecallHuntGroup(hunt_group_id=new_recall_id, option=CallParkRecall.hunt_group_only))
        try:
            cpa.update(location_id=location_id, callpark_id=target_details.callpark_id,
                       settings=settings)
            details_after = cpa.details(location_id=location_id, callpark_id=target_details.callpark_id)
            self.assertEqual(new_recall_id, details_after.recall.hunt_group_id or '')
            self.assertEqual(new_recall_name, details_after.recall.hunt_group_name)
            self.assertEqual(CallParkRecall.hunt_group_only, details_after.recall.option)
        finally:
            # restore old settings
            target_details.recall.hunt_group_id = recall_hunt_group_id
            cpa.update(location_id=location_id,
                       callpark_id=target_details.callpark_id,
                       settings=target_details)
            details = cpa.details(location_id=location_id,
                                  callpark_id=target_details.callpark_id)
            self.assertEqual(target_details.recall.hunt_group_id, details.recall.hunt_group_id)

    def test_004_from_details(self):
        """
        get details and use details for update
        """
        cpa = self.api.telephony.callpark
        new_id = cpa.update(location_id=self.target.location_id,
                            callpark_id=self.target.callpark_id,
                            settings=self.target)
        details = cpa.details(location_id=self.target.location_id,
                              callpark_id=new_id)
        details.location_id = self.target.location_id
        details.location_name = self.target.location_name
        self.assertEqual(self.target, details)

    @skip('Not implemented')
    def test_005_add_agent(self):
        """
        add an agent to a call park
        """
        # TODO: implement
        pass

    @skip('Not implemented')
    def test_006_remove_agent(self):
        """
        remove an agent from a call park
        """
        # TODO: implement
        pass


class AvailableAgents(TestWithLocations):
    """
    test available_agents()
    """

    def test_001_all(self):
        """
        available agents for all locations
        """
        with ThreadPoolExecutor() as pool:
            available_agents = list(pool.map(
                lambda location: list(self.api.telephony.callpark.available_agents(location_id=location.location_id)),
                self.locations))
        name_len = max(len(location.name) for location in self.locations)
        for location, agents in zip(self.locations, available_agents):
            print(f'Available in {location.name:{name_len}}:'
                  f' {", ".join(f"{agent.display_name} ({agent.user_type.name})" for agent in agents)}')


class AvailableRecalls(TestWithLocations):
    """
    test available_recalls()
    """

    def test_001_all(self):
        """
        available recalls for all locations
        """
        with ThreadPoolExecutor() as pool:
            available_recalls = list(pool.map(
                lambda location: list(self.api.telephony.callpark.available_recalls(location_id=location.location_id)),
                self.locations))
        name_len = max(len(location.name) for location in self.locations)
        for location, recalls in zip(self.locations, available_recalls):
            print(f'Available in {location.name:{name_len}}:'
                  f' {", ".join(recall.name for recall in recalls)}')


class TestLocationCallParkSettings(TestWithLocations):
    """
    get/update LocationCallParkSettings
    """

    def test_001_get_all(self):
        """
        get LocationCallParkSettings for all locations
        """
        with ThreadPoolExecutor() as pool:
            settings = list(pool.map(
                lambda location: self.api.telephony.callpark.call_park_settings(location_id=location.location_id),
                self.locations))
        print(f'Got call park location settings for {len(settings)} locations')

    @skip('Not implemented')
    def test_002_update_all(self):
        """
        get settings and use fill settings for update
        """
        # TODO: implement
        pass

    @skip('Not implemented')
    def test_003_recall_hg_id(self):
        """
        change recall hunt group id
        """
        # TODO: implement
        pass

    @skip('Not implemented')
    def test_004_recall_hg_option(self):
        """
        change recall hunt group option
        """
        # TODO: implement
        pass

    @skip('Not implemented')
    def test_005_cp_settings_ring_pattern(self):
        """
        change ring_pattern
        """
        # TODO: implement
        pass

    @skip('Not implemented')
    def test_006_cp_settings_recall_time(self):
        """
        change recall_time
        """
        # TODO: implement
        pass

    @skip('Not implemented')
    def test_007_cp_settings_hunt_wait_time(self):
        """
        change hunt_wait_time
        """
        # TODO: implement
        pass
