import logging
import re
import sys
from collections.abc import Generator, Iterable
from dataclasses import dataclass, field
from io import StringIO
from itertools import chain
from typing import Union, Optional, NamedTuple, ClassVar

from bs4 import BeautifulSoup, ResultSet, Tag
from inflection import underscore
from pydantic import BaseModel, Field, validator
from selenium import webdriver
from selenium.common import TimeoutException, StaleElementReferenceException
from selenium.webdriver.chromium.webdriver import ChromiumDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from yaml import safe_load, safe_dump

# foo !!!

__all__ = ['MethodDoc', 'SectionDoc', 'AttributeInfo', 'Parameter', 'MethodDetails', 'DocMethodDetails',
           'DevWebexComScraper', 'Credentials', 'SectionAndMethodDetails', 'Class']

# "Standard" menu titles we want to ignore when pull method details from submenus on the left
IGNORE_MENUS = {
    'BroadWorks Billing Reports',
    'BroadWorks Device Provisioning',
    'BroadWorks Enterprises',
    'BroadWorks Subscribers',
    'Recording Report',
    'Video Mesh',
    'Wholesale Billing Reports',
    'Wholesale Customers',
    'Wholesale Subscribers'
}

# set this to limit scraping to a subset of menus; mainly for debugging
RELEVANT_MENUS = {
    'Call Controls'
}

log = logging.getLogger(__name__)


def debugger() -> bool:
    """
    Check if executed in debugger
    """
    return (gt := getattr(sys, 'gettrace', None)) and gt()


def div_repr(d) -> str:
    """
    Simple text representation of a div
    """
    if d is None:
        return 'None'
    assert d.name == 'div'
    classes = d.attrs.get('class', None)
    if classes:
        class_str = f" class={' '.join(classes)}"
    else:
        class_str = ''
    return f'<div{class_str}>'


class Credentials(NamedTuple):
    user: str
    password: str


class MethodDoc(BaseModel):
    #: HTTP method
    http_method: str
    #: API endpoint URL
    endpoint: str
    #: link to documentation page
    doc_link: str
    #: Documentation
    doc: str


class SectionDoc(BaseModel):
    """
    Available documentation for one section on developer.webex.com

    For example for Calling/Reference/Locations
    """
    #: menu text from the menu at the left under Reference linking to the page with the list of methods
    menu_text: str
    #: list of methods parsed from the page
    methods: list[MethodDoc]


@dataclass
class AttributeInfo:
    path: str
    parameter: 'Parameter'


class Parameter(BaseModel):
    name: str
    type: str
    type_spec: Optional[str]
    doc: str
    # parsed from params-type-non-object: probably an enum
    param_attrs: Optional[list['Parameter']] = Field(default_factory=list)
    # parsed from params-type-object: child object
    param_object: Optional[list['Parameter']] = Field(default_factory=list)
    # reference to Class object; us set during class generation. Not part of (de-)serialization
    param_class: 'Class' = Field(default=None)

    @validator('param_attrs', 'param_object')
    def attrs_and_object(cls, v):
        if not v:
            return list()
        return v

    def attributes(self, *, path: str) -> Generator[AttributeInfo, None, None]:
        yield AttributeInfo(parameter=self, path=f'{path}/{self.name}')
        for p in self.param_attrs or list():
            yield from p.attributes(path=f'{path}/{self.name}/attrs')
        for p in self.param_object or list():
            yield from p.attributes(path=f'{path}/{self.name}/object')

    def dict(self, exclude=None, **kwargs):
        return super().dict(exclude={'param_class'}, **kwargs)

    def json(self, exclude=None, **kwargs):
        return super().dict(exclude={'param_class'}, **kwargs)


@dataclass
class Class:
    #: registry of Class instances by name
    registry: ClassVar[dict[str, 'Class']] = dict()

    #: logger
    log: ClassVar[logging.Logger] = logging.getLogger(f'{__name__}.Class')

    #: class name
    name: str
    _name: str = field(init=False, repr=False, default=None)

    #: attribute list
    attributes: list[Parameter] = field(default_factory=list)

    is_enum: bool = field(default=False)

    base: str = field(default=None)

    source_generated: bool = field(default=False)

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, new_name: str) -> None:
        if isinstance(new_name, property):
            raise TypeError('missing mandatory parameter: ''name''')
        if self._name is not None:
            # unregister old name
            self.registry.pop(self._name, None)
        self._name = new_name
        # register new name
        self.register()

    def register(self):
        """
        register instance
        """
        # we want to make sure that Class names are unique
        if self.registry.get(self._name) is not None:
            # suffix an index and pick the 1st name not taken
            new_name = next(name for i in range(1, 100)
                            if self.registry.get(name := f'{self._name}{i}') is None)
            self._name = new_name
        self.registry[self._name] = self

    def equivalent(self, other: 'Class'):
        """
        check if both have the same attributes
        :param other:
        :return:
        """
        if other.base == self._name:
            return True
        if self.base == other._name:
            return True
        if len(self.attributes) != len(other.attributes):
            return False
        other_attrs = {a.name: a for a in other.attributes}
        for a1 in self.attributes:
            a2 = other_attrs.get(a1.name)
            if a2 is None:
                return False
            if a1.type != a2.type:
                return False
            if (not not a1.param_class) != (not not a2.param_class):
                return False
            if a1.param_class and not a1.param_class.equivalent(a2.param_class):
                return False
        return self.base == other.base

    def sources(self) -> Generator[str, None, None]:
        """
        Source for class
        :return:
        """

        def python_type(type_str) -> str:
            if type_str == 'number':
                return 'int'
            elif type_str == 'boolean':
                return 'bool'
            elif type_str == 'string':
                return 'str'
            if (referenced_class := self.registry.get(type_str)) and referenced_class.base and \
                    not referenced_class.attributes:
                # if the referenced class has a base class and no attributes then use the name of the base class instead
                return python_type(referenced_class.base)
            return type_str

        def type_for_source(a: Parameter) -> str:
            if a.param_class:
                if a.type.startswith('array'):
                    return f'list[{python_type(a.param_class._name)}]'
                return python_type(a.param_class._name)
            if a.type.startswith('array'):
                base_type = python_type(a.type[6:-1])
                return f'list[{base_type}]'
            else:
                return python_type(a.type)

        def enum_name(a: str) -> str:
            a = re.sub(r'[^\w0-9]', '_', a)
            if '_' in a:
                a = a.lower()
            else:
                a = underscore(a)
            return a

        def handle_starting_digit(name: str) -> str:
            if name[0] in '0123456789':
                digit_name = {'0': 'zero',
                              '1': 'one',
                              '2': 'two',
                              '3': 'three',
                              '4': 'four',
                              '5': 'five',
                              '6': 'six',
                              '7': 'seven',
                              '8': 'eight',
                              '9': 'nine'}[name[0]]
                name = f'{digit_name}_{name[1:].strip("_")}'
            return name

        if self.source_generated:
            # we are done here; source already generated
            return

        if not self.attributes:
            # empty classes don't need to be generated
            return

        # 1st generate sources for all classes of any attributes
        child_classes = (attr.param_class for attr in self.attributes
                         if attr.param_class)
        for child_class in child_classes:
            yield from child_class.sources()

        # look at base
        if self.base:
            yield from self.registry[self.base].sources()

        # then yield source for this class
        source = StringIO()

        if self.base:
            bases = self.base
        elif self.is_enum:
            bases = 'str, Enum'
        else:
            bases = 'ApiModel'
        print(f'class {self._name}({bases}):', file=source)
        for attr in self.attributes:
            for line in attr.doc.splitlines():
                print(f'    #: {line}', file=source)
            if self.is_enum:
                print(f'    {handle_starting_digit(enum_name(attr.name))} = \'{attr.name}\'', file=source)
            else:
                # determine whether we need an alias
                if re.search(r'[A-Z\s]', attr.name):
                    # if the attribute name has upper case or spaces then we need an alias
                    alias = f" = Field(alias='{attr.name}')"
                    attr_name = attr.name.replace(' ', '_')
                else:
                    attr_name = attr.name
                    alias = ''
                print(f'    {handle_starting_digit(underscore(attr_name))}: Optional[{type_for_source(attr)}]{alias}',
                      file=source)
        self.source_generated = True
        yield source.getvalue()
        return

    @classmethod
    def all_sources(cls) -> Generator[str, None, None]:
        """
        Generator for all class sources

        recurse through tree of all classes and yield class sources in correct order
        :return:
        """
        yield from chain.from_iterable(map(lambda c: c.sources(), cls.registry.values()))

    def common_attributes(self, other: 'Class') -> list[Parameter]:
        """
        Get list of common attributes
        :param other:
        :return:
        """
        other_attrs = {a.name: a for a in other.attributes}
        common = list()
        for attr in self.attributes:
            if (other_attr := other_attrs.get(attr.name)) is None:
                continue
            other_attr: Parameter
            if attr.type != other_attr.type:
                continue
            common.append(attr)
        return common

    @classmethod
    def optimize(cls):
        """
        find redundant classes
            * classes/enums with identical attribute lists
            * find classes which are subclasses of others
        :return:
        """

        def log_(msg: str, level: int = logging.DEBUG):
            log.log(msg=f'optimize: {msg}', level=level)

        def attr_list(c: Class):
            return '/'.join(sorted(a.name for a in c.attributes))

        # for all pairs or classes
        # * determine set of common (same name and type) attributes
        # * create new base classes as required
        for class_a in cls.registry.values():
            if not class_a.attributes:
                # log_(f'{class_a._name}, skipping, no attributes')
                continue
            # for now ignore multiple tiers of hierachy
            # if this class already has a base then don't check whether this class is subclass of another
            if class_a.base:
                # log_(f'{class_a._name}, skipping, base {class_a.base}')
                continue

            # look at all other classes
            for class_b in cls.registry.values():
                if class_b._name == class_a._name:
                    continue
                # if class_b already has a base then skip
                if class_b.base:
                    # log_(f'{class_a._name}/{class_b._name}, skipping, {class_b._name} has base {class_b.base}')
                    continue

                # determine common attributes
                common = class_a.common_attributes(class_b)
                if len(common) == len(class_a.attributes) and len(common) > 1:
                    log_(f'{class_a._name}/{class_b._name}, common attributes: {", ".join(a.name for a in common)}')

                    # of all class_a attributes also exist in class_b then class_a is subclass of class_b
                    class_b.base = class_a._name
                    # ... and we can remove all common attributes from class_b
                    names = {a.name for a in common}
                    class_b.attributes = [a for a in class_b.attributes
                                          if a.name not in names]
                # if
            # for
        # for
        return


class MethodDetails(BaseModel):
    header: str
    doc: str
    parameters_and_response: dict[str, list[Parameter]]
    documentation: MethodDoc

    def attributes(self, *, path: str) -> Generator[AttributeInfo, None, None]:
        for pr_key in self.parameters_and_response:
            for p in self.parameters_and_response[pr_key]:
                yield from p.attributes(path=f'{path}/{self.header}/{pr_key}')


class SectionAndMethodDetails(NamedTuple):
    section: str
    method_details: MethodDetails

    def __lt__(self, other: 'SectionAndMethodDetails'):
        return self.section < other.section or self.section == other.section and (
                self.method_details.documentation.endpoint < other.method_details.documentation.endpoint or
                self.method_details.documentation.endpoint == other.method_details.documentation.endpoint and
                self.method_details.documentation.http_method < other.method_details.documentation.http_method)


class DocMethodDetails(BaseModel):
    """
    Container for all information; interface to YML file
    """
    info: Optional[str]
    #: dictionary indexed by menu text with list of methods in that section
    docs: dict[str, list[MethodDetails]] = Field(default_factory=dict)

    @staticmethod
    def from_yml(path: str):
        with open(path, mode='r') as f:
            return DocMethodDetails.parse_obj(safe_load(f))

    def to_yml(self, path: Optional[str] = None) -> Optional[str]:
        data = self.dict()
        if path:
            with open(path, mode='w') as f:
                if self.info:
                    line = '# ' + f'{self.info}' + '\n'
                    f.write(line)
                safe_dump(data, f)
            return None
        else:
            return safe_dump(data)

    def methods(self) -> Generator[SectionAndMethodDetails, None, None]:
        for section, method_details in self.docs.items():
            for m in method_details:
                yield SectionAndMethodDetails(section=section, method_details=m)

    def attributes(self) -> Generator[AttributeInfo, None, None]:
        for method_details_key in self.docs:
            method_details = self.docs[method_details_key]
            for md in method_details:
                yield from md.attributes(path=f'{method_details_key}')

    def dict(self, exclude=None, **kwargs):
        return super().dict(exclude={'info'}, by_alias=True, **kwargs)


@dataclass
class DevWebexComScraper:
    driver: ChromiumDriver
    logger: logging.Logger
    credentials: Credentials
    baseline: Optional[DocMethodDetails]
    new_only: bool

    def __init__(self, credentials: Credentials = None, baseline: DocMethodDetails = None,
                 new_only: bool = True):
        self.driver = webdriver.Chrome()
        self.logger = logging.getLogger(f'{__name__}.{self.__class__.__name__}')
        self.credentials = credentials
        self.baseline = baseline
        self.new_only = new_only

    def close(self):
        self.log('close()')
        if self.driver:
            self.driver.quit()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.log('__exit__()')
        self.close()

    def __enter__(self):
        return self

    def log(self, msg: str, level: int = logging.DEBUG):
        self.logger.log(level=level, msg=msg)

    @staticmethod
    def by_class_and_text(find_in: Union[ChromiumDriver, WebElement], class_name: str, text: str) -> WebElement:
        """
        Find a WebElement by class name and text

        :param find_in: root to search in
        :param class_name: class name
        :param text: text
        :return: WebElement
        :raises:
            StopIteration: if no element can be found
        """
        return next((element for element in find_in.find_elements(by=By.CLASS_NAME, value=class_name)
                     if element.text == text))

    def login(self):
        """
        Log in
        :return:
        """
        if not self.credentials:
            return

        # look for: <a href="/login" id="header-login-link"><span>Log in</span></a>
        login = self.driver.find_element(by=By.ID, value='header-login-link')
        login.click()

        # enter email
        email = self.driver.find_element(by=By.ID, value='IDToken1')
        email.send_keys(self.credentials.user)

        # wait for "Sign In" button
        sign_in = WebDriverWait(driver=self.driver, timeout=10).until(
            method=EC.element_to_be_clickable((By.ID, 'IDButton2')))
        sign_in.click()
        password = WebDriverWait(driver=self.driver, timeout=10).until(
            method=EC.visibility_of_element_located((By.ID, 'IDToken2')))
        password.send_keys(self.credentials.password)

        # wait for "Sign In" button
        sign_in = WebDriverWait(driver=self.driver, timeout=10).until(
            method=EC.element_to_be_clickable((By.ID, 'Button1')))
        sign_in.click()

        return

    @staticmethod
    def obj_class(tag: Tag) -> set[str]:
        """
        Check if a tag has one of the classes that designate an object

        :param tag:
        :return: set of tag classes designating an object
        """
        classes = set(tag.attrs.get('class', []))
        return classes & {'params-type-non-object', 'params-type-object'}

    def methods_from_api_reference_container(self, container: BeautifulSoup,
                                             header: str) -> Generator[MethodDoc, None, None]:
        """
        Yield method documentation instances for each method parsed from an API reference container on the right

        Container looks like:
            <div class="api_reference_entry__container">
                <div class="columns large-9">
                    <div class="XZCMfprZP3RvdJJ_CfTH"><h3>Locations</h3>
                        <p>Locations are used to organize Webex Calling (BroadCloud) features within physical
                        locations. Y .... .</p></div>
                    <div>
                        <div class="clearfix dVWBljZUFIGiEgkvpTg5"><h5 class="columns
                        small-9"><span>Method</span></h5><h5
                                class="columns small-3"><span>Description</span></h5></div>
                        <div class="UuvKF2Qey4J1ajkvpmiN">
                            <div class="B_af0kjJ65j92iCwAiw1">
                                <div class="columns small-9"><span
                                        class="md-badge md-badge--green E38yq9G_nWIm8SdrG1GU">GET</span><span
                                        class="X9_XSxV8TI6eNf98ElQU"><a
                                        href="/docs/api/v1/locations/list-locations">https://webexapis.com/v1
                                        /locations</a></span>
                                </div>
                                <div class="columns small-3 sn1OrZrRvd9GVOvUv4WK">List Locations</div>
                            </div>
                            <div class="cg3iKW8BWwV8ooZrsjQi">
                                <div class="X9_XSxV8TI6eNf98ElQU"><a
                                href="/docs/api/v1/locations/list-locations">https://webexapis.com/v1/locations</a>
                                </div>
                                <span class="md-badge md-badge--green E38yq9G_nWIm8SdrG1GU">GET</span><span
                                    class="sn1OrZrRvd9GVOvUv4WK">List Locations</span></div>
                        </div>

        :param container: API reference container
        :param header: header these methods belong under; for logging
        """

        def log(text: str, level: int = logging.DEBUG):
            self.log(f'    methods_from_api_reference_container("{header}"): {text}',
                     level=level)

        log('start')
        rows = container.div.div.find_all('div', recursive=False)[1].find_all('div', recursive=False)
        """
            Rows look like this:
                <div class="B_af0kjJ65j92iCwAiw1">
                    <div class="columns small-9"><span class="md-badge md-badge--green 
                    E38yq9G_nWIm8SdrG1GU">GET</span><span
                            class="X9_XSxV8TI6eNf98ElQU"><a
                            href="/docs/api/v1/broadworks-billing-reports/list-broadworks-billing-reports">https
                            ://webexapis.com/v1/broadworks/billing/reports</a></span>
                    </div>
                    <div class="columns small-3 sn1OrZrRvd9GVOvUv4WK">List BroadWorks Billing Reports</div>
                </div>
                <div class="cg3iKW8BWwV8ooZrsjQi">
                    <div class="X9_XSxV8TI6eNf98ElQU"><a 
                    href="/docs/api/v1/broadworks-billing-reports/list-broadworks-billing-reports">https://webexapis
                    .com/v1/broadworks/billing/reports</a>
                    </div>
                    <span class="md-badge md-badge--green E38yq9G_nWIm8SdrG1GU">GET</span><span 
                    class="sn1OrZrRvd9GVOvUv4WK">List BroadWorks Billing Reports</span>
                </div>
        """
        for soup_row in rows[1:]:
            method = soup_row.div.div.span.text
            endpoint = soup_row.div.div.a.text
            doc_link = f"https://developer.webex.com{soup_row.div.div.a.get('href')}"
            doc = soup_row.div.find_all('div')[1].text

            log(f'{doc}', level=logging.INFO)
            log(f'yield: {method} {endpoint}: {doc}, {doc_link}', level=logging.DEBUG)
            yield MethodDoc(http_method=method, endpoint=endpoint, doc_link=doc_link, doc=doc)
        log('end')

    def docs_from_submenu_items(self, submenus: list[WebElement]) -> Generator[SectionDoc, None, None]:
        """
        Yield section information for each submenu on the left
        :param submenus:
        :return:
        """

        def log(text: str, level: int = logging.DEBUG):
            self.log(f'  endpoints_from_submenu_items({submenu.text}): {text}',
                     level=level)

        prev_container_header = None

        def wait_for_new_api_reference_container():
            """
            Wait until the page on teh right has been updated with new content after clicking on a new section on the
            left
            """

            def log(text: str):
                self.log(f'  wait_for_new_api_reference_container: {text}')

            def _predicate(driver):
                """
                Look for API reference container and check if the container header has changed
                """
                target = driver.find_element(By.CLASS_NAME, 'api_reference_entry__container')
                log('Container found' if target else 'Container not found')
                target = EC.visibility_of(target)(driver)
                log(f'Visibility: {not not target}')
                if target:
                    target: WebElement
                    # header selector: div.api_reference_entry__container > div > div:nth-of-type(1) > h3
                    container_header = target.find_element(
                        by=By.CSS_SELECTOR,
                        value='div > div:nth-of-type(1) > h3')

                    header_text = container_header.text
                    log(f'prev container header: {prev_container_header}, header: {header_text}')
                    if header_text != prev_container_header:
                        return target, header_text
                return False

            return _predicate

        for submenu in submenus:
            # decide whether we need to work on the sub menu
            submenu_text = submenu.text
            ignore = False
            if self.baseline:
                if self.new_only:
                    # only work on menus not present in the diff
                    if submenu_text in self.baseline.docs:
                        ignore = True
                else:
                    # skip if the baseline has the menu, but no methods. This is one of the groups we want to ignore
                    if (submenu_text in self.baseline.docs) and not self.baseline.docs[submenu_text]:
                        ignore = True
            else:
                # .. skip all non-"standard" menus
                if submenu.text in IGNORE_MENUS:
                    ignore = True
                if False and debugger() and submenu_text not in RELEVANT_MENUS:
                    ignore = True
            if ignore:
                # .. skip
                log(f'skipping', level=logging.INFO)
                # .. but yield an empty list, so that we at least have a marker for that section
                yield SectionDoc(menu_text=submenu.text,
                                 methods=list())
                continue

            log(f'Extracting methods from "{submenu.text}" menu', level=logging.INFO)

            log('start')
            log('click()')

            # click on the submenu on the left
            submenu.click()

            # after clicking on the submenu we need to wait for a new api reference container to show up
            try:
                for i in range(3):
                    try:
                        api_reference_container, header = WebDriverWait(driver=self.driver, timeout=10).until(
                            method=wait_for_new_api_reference_container())
                    except StaleElementReferenceException:
                        if i < 2:
                            continue
                        raise
                    else:
                        break
            except TimeoutException:
                api_reference_container = None
                log('!!!!! Timeout waiting for documentation window to show up !!!!!', level=logging.ERROR)
                continue
            api_reference_container: WebElement
            header: str

            # set the new header (needed when waiting for the next container)
            prev_container_header = header

            soup = BeautifulSoup(api_reference_container.get_attribute('outerHTML'), 'html.parser')
            yield SectionDoc(menu_text=submenu.text,
                             methods=list(self.methods_from_api_reference_container(
                                 container=soup,
                                 header=submenu.text)))
            log('end')
        return

    def get_calling_docs(self) -> list[SectionDoc]:
        """
        Read developer.webex.com and get doc information for all endpoints under "Calling"
        """
        url = 'https://developer.webex.com/docs'

        def log(text: str, level: int = logging.DEBUG):
            self.log(level=level, msg=f'navigate_to_calling_reference: {text}')

        log(f'opening "{url}"')
        self.driver.get(url)

        # wait max 10 seconds for accept cookies button to show up and be steady

        log('waiting for button to accept cookies')

        def steady(locator):
            """
            Wait for a web element to be:
                * visible
                * enabled
                * steady: same position at two consecutive polls
            :param locator:
            :return: False or web element
            """
            #: mutable to cache postion of element
            mutable = {'pos': dict()}

            def log(text: str):
                self.log(f'steady: {text}')

            def _predicate(driver):
                target = driver.find_element(*locator)
                target = EC.visibility_of(target)(driver)
                if target and target.is_enabled():
                    target: WebElement
                    pos = target.location
                    log(f'prev pos: {mutable["pos"]}, pos: {pos}')
                    if mutable['pos'] == pos:
                        return target
                    mutable['pos'] = pos
                else:
                    log(f'not visible or not enabled')
                return False

            return _predicate

        try:
            # wait for button to accept cookies to be steady
            accept_cookies = WebDriverWait(driver=self.driver, timeout=10).until(
                method=steady((By.ID, 'onetrust-accept-btn-handler')))
        except TimeoutException:
            # if there is no accept cookies button after 10 seconds then we are probably ok
            log('No popup to accept cookies', level=logging.WARNING)
        else:
            accept_cookies: WebElement
            log('accept cookies')
            accept_cookies.click()

        if self.credentials:
            self.login()

        log('looking for "Calling"')
        calling = self.by_class_and_text(find_in=self.driver,
                                         class_name='md-list-item__center',
                                         text='Calling')
        log('clicking on "Calling"')
        calling.click()

        # after clicking on "Calling" an expanded nav group exists
        log('looking for expanded sidebar nav group')
        calling_nav_group = WebDriverWait(driver=self.driver, timeout=10).until(
            method=EC.presence_of_element_located((By.CLASS_NAME, 'md-sidebar-nav__group--expanded')))

        # in that nav group we want to click on "Reference"
        log('looking for "Reference" in expanded sidebar group')
        reference = self.by_class_and_text(find_in=calling_nav_group,
                                           class_name='md-list-item__center',
                                           text='Reference')
        log('clicking on "Reference"')
        reference.click()

        # After clicking on "Reference" a new expanded nav group should exist
        log('Looking for expanded sidebar nav group under "Calling"')
        reference_nav_group = next(iter(calling_nav_group.find_elements(by=By.CLASS_NAME,
                                                                        value='md-sidebar-nav__group--expanded')))
        log('Collecting menu items in "Reference" sidebar group')
        reference_items = reference_nav_group.find_elements(by=By.CLASS_NAME, value='md-submenu__item')
        log(f"""menu items in "Reference" sidebar group: {', '.join(f'"{smi.text}"' for smi in reference_items)}""")

        docs = list(self.docs_from_submenu_items(reference_items))
        return docs

    def param_parser(self, divs: Iterable[Tag], level: int = 0) -> Generator[Parameter, None, None]:
        """
        Parse parameters from divs
        :param divs:
        :return:
        """

        param_div = None
        name = None

        def log(msg: str, div: Tag = None, log_level: int = logging.DEBUG):
            div = div or param_div
            name_str = name and f'"{name}", ' or ""
            self.log(f'      {" " * level}param_parser({div_repr(div)}): {name_str}{msg}', level=level)

        def div_generator(div_list: list[Tag]) -> Generator[Tag, None, None]:
            """
            Generator for divs to consider in parser

            The generator takes care of climbing down div hierarchies and trying to hide other "anomalies" from the
            parser
            :param div_list:
            :return:
            """
            for tag in div_list:
                div = tag
                yield_div = True
                while True:
                    if div.attrs.get('class', None) is None:
                        log('div_generator: classless div->yield immediately')
                        break

                    # also yield divs indicating an object
                    if classes := self.obj_class(div):
                        log(f'div_generator: div indicating an object({next(iter(classes))})->yield immediately')
                        break

                    # if this div only has one div child then go one down
                    # for a parameter we expect two child divs
                    if len(div.find_all('div', recursive=False)) == 1:
                        div = div.div
                        log('div_generator: div with single div child. moved one down')
                        continue
                    # if there is a button then go one down
                    if div.find('button', recursive=False):
                        div = div.div
                        log('div_generator: found a button, went one down')
                        continue
                    """
                    if we have a list of classless divs as childs then yield the childs
                    example:
                        <div class="emjDUw5LqTp3QCCg4hNp">
                            <div class="bfIcOqrr0LEmWxjEID2z">
                                <div class="Sj3x8PGVKM_DQu1MaOpF">
                                    <div>
                                        <div class="bfIcOqrr0LEmWxjEID2z">
                                            <div class="ETdjpkOd18yDmr_Pomer">
                                                <div class="AzemgtvlBWwLVUYkRkbg">id</div>
                                                <div class="Xjm2mpYxY4YHNn4XsTBg"><span>string</span><span 
                                                class="buEuRUqtw7z8xim5DxxA">required</span>
                                                </div>
                                            </div>
                                            <div class="Sj3x8PGVKM_DQu1MaOpF"><p>Unique ID for the rule.</p></div>
                                        </div>
                                    </div>
                                    <div>
                                        <div class="bfIcOqrr0LEmWxjEID2z">
                                            <div class="ETdjpkOd18yDmr_Pomer">
                                                <div class="AzemgtvlBWwLVUYkRkbg">enabled</div>
                                                <div class="Xjm2mpYxY4YHNn4XsTBg"><span>boolean</span></div>
                                            </div>
                                            <div class="Sj3x8PGVKM_DQu1MaOpF"><p>Reflects if rule is enabled.</p></div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    """
                    if (childs := div.find_all('div', recursive=False)) and all(child.attrs.get('class', None) is None
                                                                                for child in childs):
                        log(f'div_generator: list of classless child divs -> yield divs from childs')
                        for child in childs:
                            child: Tag
                            yield from child.find_all('div', recursive=False)
                        yield_div = False
                    break
                if yield_div:
                    yield div
            return

        log(f'start: divs({len(divs)}): {", ".join(map(div_repr, divs))}')
        div_iter = div_generator(divs)
        param_div = next(div_iter, None)
        while param_div:
            name = None
            # a classless div is a wrapper for a list of attributes
            if param_div.attrs.get('class', None) is None:
                # yield members of classless div
                yield from self.param_parser(param_div.find_all('div', recursive=False),
                                             level=level)
                # and then continue with the next
                param_div = next(div_iter, None)
                continue

            # special case: a div w/o child divs and just two spans
            if not param_div.find_all('div', recursive=False):
                spans = param_div.find_all('span', recursive=False)
                if len(spans) == 2:
                    yield Parameter(name=spans[0].text,
                                    type='',
                                    doc=f'{spans[0].text}{spans[1].text}')
                param_div = next(div_iter, None)
                continue

            param_attrs = None
            param_object = None

            # the div should have two child divs:
            #   * attribute name and type
            #   * attribute doc string
            child_divs = param_div.find_all('div', recursive=False)
            log(f'child divs({len(child_divs)}): {", ".join(map(div_repr, child_divs))}')

            if len(child_divs) == 2:
                """
                Parse something like this
                    <div class="bfIcOqrr0LEmWxjEID2z">
                        <div class="ETdjpkOd18yDmr_Pomer">
                            <div class="AzemgtvlBWwLVUYkRkbg">personId</div>
                            <div class="Xjm2mpYxY4YHNn4XsTBg"><span>string</span><span 
                            class="buEuRUqtw7z8xim5DxxA">required</span></div>
                        </div>
                        <div class="Sj3x8PGVKM_DQu1MaOpF"><p>Unique identifier for the person.</p></div>
                    </div>
                """
                param_div, p_spec_div = child_divs

                # get attribute name and type
                child_divs = param_div.find_all('div', recursive=False)
                log(f'param child divs({len(child_divs)}): {", ".join(map(div_repr, child_divs))}')
                assert len(child_divs) == 2
                name_div, type_div = child_divs
                name = name_div.text

                # type information has type and addtl. spec in spans
                spans = type_div.find_all('span', recursive=False)
                log(f'# of spans in type spec: {len(spans)}')
                assert len(spans) and len(spans) <= 2
                param_type = spans[0].text
                if len(spans) == 2:
                    type_spec = spans[1].text
                else:
                    type_spec = None

                # catch "callOfferToneEnabled `true`"
                if param_type == 'boolean' and len(name.split()) > 1:
                    name = name.split()[0]

                # doc is in the second div
                doc_paragraphs = p_spec_div.find_all('p', recursive=False)
                doc = '\n'.join(map(lambda p: p.text, doc_paragraphs))

                # for an enum the second div can have a list of enum values
                child_divs = p_spec_div.find_all('div', recursive=False)
                if child_divs:
                    log(f'divs in second div of parameter parsed ({len(child_divs)}): '
                        f'{", ".join(map(div_repr, child_divs))}')
                    param_attrs = list(self.param_parser(child_divs, level=level + 1)) or None
                    if param_attrs and len(param_attrs) == 1 and not any(
                            (param_attrs[0].param_attrs, param_attrs[0].param_object)):
                        # a single child attribute without childs doesn't make any sense
                        # instead add something to the doc string
                        doc_line = param_attrs[0].doc.strip()
                        log(f'single child attribute doesn\'t make sense. Adding line to documentation: "{doc_line}"')
                        doc = '\n'.join((doc.strip(), doc_line))
                        param_attrs = None
                    foo = 1
            elif len(child_divs) < 3:
                # to short: not idea what we can do here....
                log(f'to few divs: {len(child_divs)}: skipping')
                param_div = next(div_iter, None)
                continue
            else:
                """
                Special case:
                    <div class="emjDUw5LqTp3QCCg4hNp">
                        <div class="AzemgtvlBWwLVUYkRkbg">primary</div>
                        <div class="Xjm2mpYxY4YHNn4XsTBg"><span>boolean</span></div>
                        <div class="Sj3x8PGVKM_DQu1MaOpF"><p>Flag to indicate if the number is primary or 
                        not.</p></div>
                        <div class="Mo4RauPOboRxtDGO9VvT"><span>Possible values: </span><span></span></div>
                    </div>      
                """
                childs = iter(child_divs)

                # get name of attribute from 1st div
                # <div class="AzemgtvlBWwLVUYkRkbg">primary</div>
                name = next(childs).text
                log(f'flat sequence of divs')

                # next div has a list of spans ...
                # <div class="Xjm2mpYxY4YHNn4XsTBg"><span>boolean</span></div>
                spans = iter(next(childs).find_all('span', recursive=False))

                # .. and the 1st span has the type
                param_type = next(spans).text

                # ... if there is still one span then that's the type spec
                span = next(spans, None)
                type_spec = span and span.text

                # the next div has a list of paragraphs with the documentation
                # <div class="Sj3x8PGVKM_DQu1MaOpF"><p>Flag to indicate if the number is primary or not.</p></div>
                doc = '\n'.join(p.text
                                for p in next(childs).find_all('p', recursive=False))

                # there might be one more div
                # <div class="Mo4RauPOboRxtDGO9VvT"><span>Possible values: </span><span></span></div>
                div = next(childs, None)
                if div:
                    # ... with a list of spans; add the text in these spans to the doc
                    spans = iter(div.find_all('span', recursive=False))
                    doc_line = f'{next(spans).text}{", ".join(t for s in spans if (t := s.text))}'
                    log(f'enhancing doc string: "{doc_line}"')
                    doc = '\n'.join((doc, doc_line))

            # if

            # look ahead to next div
            param_div = next(div_iter, None)
            if param_div:
                # check if class is one of the param classes
                if classes := self.obj_class(param_div):
                    log(f'parsing next div ({next(iter(classes))}) as part of this parameter')
                    # this enhances the current parameter
                    obj_attributes = list(self.param_parser(param_div.find_all('div', recursive=False),
                                                            level=level + 1)) or None
                    if 'params-type-non-object' in classes:
                        assert param_attrs is None
                        param_attrs = obj_attributes
                    else:
                        param_object = obj_attributes
                    # move to next div
                    param_div = next(div_iter, None)
                # if
            # if

            # ignore string parameters with invalid names
            # we can keep spaces and slashes. These will be transformed to correct names when creating the classes
            if not re.match(r'^[^0-9#][\w\s/]*$', name):
                if name == '#':
                    name = 'hash'
                elif name in '0123456789':
                    name = f'digit_{name}'
                elif len(name.split()) > 1:
                    log(f'ignoring parameter name "{name}"', log_level=logging.WARNING)
                    continue

            if param_type == 'enum' and not param_attrs and not param_object:
                log(f'type "enum" without attributes transformed to "string"')
                param_type = 'string'

            log(f'yield type={param_type}, type_spec={type_spec}, '
                f'param_attrs={param_attrs and len(param_attrs) or 0}, '
                f'param_object={param_object and len(param_object) or 0}')
            yield Parameter(name=name,
                            type=param_type,
                            type_spec=type_spec,
                            doc=doc,
                            param_attrs=param_attrs,
                            param_object=param_object)
        # while
        return

    def params_and_response_from_divs(self, divs: ResultSet) -> dict[str, list[Parameter]]:
        """
        Extract params and response properties from child divs of api-reference__description
        :param divs:
        :return:
        """

        def log(msg: str):
            self.log(f'    params_and_response_from_divs: {msg}')

        log('start')
        result: dict[str, list[Parameter]] = {}
        for div in divs:
            # each div has one or more h6 headers and the same number of divs of class vertical-up with the parameter
            # information
            if div.attrs.get('class', None) is None:
                # navigate one level down if encapsulated in an empty div: this is the case for "Response Properties"
                div = div.div
                log(f'navigating one level down from <div> to {div_repr(div)}')
                if div is None:
                    # apparently this was an empty div; we are done here
                    continue
            headers = div.find_all(name='h6', recursive=False)
            parameter_groups = div.find_all(class_='vertical-up', recursive=False)
            assert len(headers) == len(parameter_groups)
            log(f"""{div_repr(div)}: headers({len(headers)}): {", ".join(map(lambda h: f'"{h.text}"', headers))}""")

            for header, parameters in zip(headers, parameter_groups):
                if False and debugger() and header.text != 'Body Parameters':
                    continue
                # each parameter spec is in one child div
                child_divs = parameters.find_all(name='div', recursive=False)
                log(f'{div_repr(div)}, header("{header.text}"). child divs({len(child_divs)}): '
                    f'{", ".join(map(div_repr, child_divs))}')
                # parsed_params = list(map(self.parse_param, child_divs))
                parsed_params = list(self.param_parser(child_divs))
                result[header.text] = parsed_params
        log('end')
        return result

    def get_method_details(self, method_doc: MethodDoc) -> Optional[MethodDetails]:
        """
        Get details for one method

        :param method_doc:
        :return:
        """

        def log(msg: str, level: int = logging.DEBUG):
            self.log(f'  get_method_details("{method_doc.doc}"): {msg}',
                     level=level)

        if False and debugger() and method_doc.doc != 'Reject':
            # skip
            return

        log('', level=logging.INFO)

        doc_link = method_doc.doc_link

        # sometimes links have a superfluous trailing dot
        # we try the original URL 1st and retry w/p trailing dots
        while True:
            # navigate to doc url of method
            log(f'GET {doc_link}')
            self.driver.get(doc_link)

            # we don't need to click on anything. Hence we can just extract from static page using BeautifulSoup
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')

            api_ref_descr = soup.find(class_='api-reference__description')
            """ API reference description is something like:
                    <div class="columns u_6eoYxPfVMJxwlI0Wcb large-6 xlarge-6 api-reference__description 
                    XdQBFUuam5J29sqNCtir">
                        <div class="K_M3cdOQTnTLnPOWhtqe"><h4>Read Person's Calling Behavior</h4>
                            <div><p>Retrieves the calling behavior and UC Manager Profile settings for the person which 
                            includes overall
                                calling behavior and calling UC Manager Profile ID.</p>
                                <p>Webex Calling Behavior controls which Webex telephony application and which UC 
                                Manager 
                                Profile is to be
                                    used for a person.</p>
                                </div>
            """
            if not api_ref_descr:
                if doc_link.endswith('.'):
                    log(f'GET {doc_link} failed. Retry w/o trailing "."',
                        level=logging.WARNING)
                    doc_link = doc_link.strip('.')
                    continue
                log(f'GET failed? API reference description not found on page', level=logging.ERROR)
                return None
            break
        # while

        try:
            header = api_ref_descr.div.h4.text
        except AttributeError:
            log(f'Failed o parse header from api spec',
                level=logging.ERROR)
            return None

        log(f'header from API reference description: "{header}"')

        # long doc string can have multiple paragraphs
        doc_paragraphs = api_ref_descr.div.div.find_all(name='p', recursive=False)
        assert doc_paragraphs
        long_doc_string = '\n'.join(dp.text for dp in doc_paragraphs)

        # parameters and response values are in the divs following the 1st one. The last div has response codes
        # hence to get parameters and response codes we can skip the 1st and last div
        divs = api_ref_descr.find_all(name='div', recursive=False)

        divs = divs[1:-1]

        log(f'child divs for parameters and response: {", ".join(map(div_repr, divs))}')
        params_and_response = self.params_and_response_from_divs(divs)
        result = MethodDetails(header=header,
                               doc=long_doc_string,
                               parameters_and_response=params_and_response,
                               documentation=method_doc)
        log('end')
        return result
