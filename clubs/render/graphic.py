import copy
import http.client
import math
import multiprocessing
import os
import socket
import time
import flask
import flask_socketio
import urllib.error
import urllib.request
from multiprocessing import connection
from typing import Any, Dict, List, Optional, Tuple, Union, overload
from xml.etree import ElementTree as et

from .. import error, poker
from . import viewer


class GraphicViewer(viewer.PokerViewer):
    def __init__(
        self,
        num_players: int,
        num_hole_cards: int,
        num_community_cards: int,
        host: str = "127.0.0.1",
        port: int = 0,
        **kwargs: Any,
    ):
        super(GraphicViewer, self).__init__(
            num_players, num_hole_cards, num_community_cards, **kwargs
        )
        self.host = host
        if port:
            self.port = port
        else:
            tmp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tmp_socket.bind(("", 0))
            self.port = tmp_socket.getsockname()[1]
            tmp_socket.close()

        self.svg_poker = _SVGPoker(
            self.num_players, self.num_hole_cards, self.num_community_cards
        )

        self.process = multiprocessing.Process(target=self._run_flask)
        self.process.start()

        self._test_socket_conn()
        self._test_flask_conn()

    def _test_socket_conn(self) -> None:
        start = time.time()
        while True:
            try:
                self.socket = connection.Client((self.host, self.port + 1))
                break
            except (ConnectionRefusedError, OSError):
                time.sleep(0.01)
                if time.time() - start > 10:
                    raise error.RenderInitializationError(
                        "unable to connect to flask process socket"
                    )

    def _test_flask_conn(self) -> None:
        start = time.time()
        response: Optional[http.client.HTTPResponse] = None
        while True:
            try:
                response = urllib.request.urlopen(f"http://{self.host}:{self.port}")
                break
            except urllib.error.URLError:
                time.sleep(0.01)
                if time.time() - start > 10:
                    raise error.RenderInitializationError(
                        "unable to connect to flask server"
                    )

        assert response and response.status == 200

    def close(self) -> None:
        if self.process.is_alive():
            self.socket.send({"content": "close"})
            self.process.terminate()
            self.process.join()

    def __del__(self) -> None:
        self.close()

    def _run_flask(self) -> None:
        from gevent import monkey

        monkey.patch_all()
        import markupsafe

        config: Dict[str, Any] = {}
        dir_path = os.path.dirname(os.path.realpath(__file__))
        templates_path = os.path.join(dir_path, "resources", "templates")
        static_path = os.path.join(dir_path, "resources", "static")
        app = flask.Flask(
            "clubs", template_folder=templates_path, static_folder=static_path
        )
        socketio = flask_socketio.SocketIO(app)

        @socketio.on("connect")
        def connect():  # type: ignore
            socketio.emit("config", config)

        @app.route("/")
        def index():  # type: ignore
            svg = str(self.svg_poker.base_svg)
            return flask.render_template("index.html", svg=markupsafe.Markup(svg))

        def listener() -> None:
            nonlocal config
            socket = connection.Listener((self.host, self.port + 1))
            conn = socket.accept()
            while True:
                if conn.poll():
                    message: Dict[str, Any] = conn.recv()
                    if message["content"] == "close":
                        conn.close()
                        break
                    else:
                        config = message["content"]
                        socketio.emit("config", config, broadcast=True)
                socketio.sleep(0.0001)
            socket.close()

        socketio.start_background_task(listener)

        socketio.run(app, port=self.port)

    def render(self, config: viewer.RenderConfig, sleep: float = 0) -> None:
        """Render the table in browser based on the table configuration

        Parameters
        ----------
        config : viewer.RenderConfig
            game configuration dictionary

        sleep : float, optional
            sleep time after render, by default 0

        Examples
        --------
        >>> from clubs import Card
        >>> config = {
        ...     'action': 0, # int - position of active player
        ...     'active': [True, True], # List[bool] - list of active players
        ...     'all_in': [False, False], # List[bool] - list of all in players
        ...     'community_cards': [], # List[Card] - list of community cards
        ...     'button': 0, # int - position of dealer button
        ...     'done': False, # bool - toggle if hand is completed
        ...     'hole_cards': [[Card("Ah")], [Card("Ac")]], # List[List[Card]] -
        ...                                                 # list of list of hole card
        ...     'pot': 10, # int - chips in pot
        ...     'payouts': [0, 0], # List[int] - list of chips won for each player
        ...     'prev_action': (1, 10, False], # Tuple[int, int, int] -
        ...                                    # last position bet and fold
        ...     'street_commits': [10, 20], # List[int] - list of number of
        ...                                 # chips added to pot from each
        ...                                 # player on current street
        ...     'stacks': [100, 100] # List[int] - list of stack sizes
        ... }
        """
        self.socket.send({"content": _jsonify(config)})
        if sleep:
            time.sleep(sleep)


@overload
def _convert_hands(hands: List[poker.Card]) -> List[str]: ...  # pragma: no cover


@overload
def _convert_hands(
    hands: List[List[poker.Card]],
) -> List[List[str]]: ...  # pragma: no cover


def _convert_hands(
    hands: Union[List[poker.Card], List[List[poker.Card]]],
) -> Union[List[str], List[List[str]]]:
    _hands: List[List[str]] = []
    _cards: List[str] = []
    for hand in hands:
        if isinstance(hand, poker.Card):
            _cards.append(str(hand))
        else:
            _cards = []
            for card in hand:
                _cards.append(str(card))
            _hands.append(_cards)
    if _hands:
        return _hands
    return _cards


def _jsonify(config: viewer.RenderConfig) -> Dict[str, Any]:
    _config: Dict[str, Any] = {**config}
    _config["hole_cards"] = _convert_hands(_config["hole_cards"])
    _config["community_cards"] = _convert_hands(_config["community_cards"])
    return _config


class _RoundedRectangle:
    def __init__(self, x: float, y: float, width: float, height: float) -> None:
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.center_x = width * 0.5
        self.center_y = height * 0.5

    def edge(self, frac: float) -> Tuple[float, float]:
        frac = frac % 1
        x, y = 0.0, 0.0
        perimeter_frac = frac * self.perimeter
        if perimeter_frac < self.straight_width * 0.5:
            x = -perimeter_frac
            y = self.radius_height
        elif perimeter_frac < self.straight_width * 0.5 + self.circle_perimeter * 0.5:
            circle_frac = (perimeter_frac - self.straight_width * 0.5) / (
                self.circle_perimeter * 0.5
            )
            angle = math.pi * 0.5 + math.pi * circle_frac
            x = self.radius_height * math.cos(angle) - self.straight_width * 0.5
            y = self.radius_height * math.sin(angle)
        elif perimeter_frac < self.straight_width * 1.5 + self.circle_perimeter * 0.5:
            straight_frac = (
                perimeter_frac - self.straight_width * 0.5 - self.circle_perimeter * 0.5
            ) / self.straight_width
            x = (straight_frac - 0.5) * self.straight_width
            y = -self.radius_height
        elif perimeter_frac < self.straight_width * 1.5 + self.circle_perimeter:
            circle_frac = (
                perimeter_frac - self.straight_width * 1.5 + self.circle_perimeter * 0.5
            ) / (self.circle_perimeter * 0.5)
            angle = math.pi * 1.5 + math.pi * circle_frac
            x = self.radius_height * math.cos(angle) + self.straight_width * 0.5
            y = self.radius_height * math.sin(angle)
        elif frac <= 1:
            straight_frac = (
                perimeter_frac - self.straight_width * 1.5 - self.circle_perimeter
            ) / self.straight_width
            x = (-straight_frac + 0.5) * self.straight_width
            y = self.radius_height
        x += self.center_x + self.x
        y += self.center_y + self.y
        return round(x, 2), round(y, 2)

    @property
    def radius_height(self) -> float:
        return float(self.height * 0.5)

    @property
    def circle_perimeter(self) -> float:
        return float(math.pi * self.height)

    @property
    def straight_width(self) -> float:
        return float(self.width - self.height)

    @property
    def perimeter(self) -> float:
        return float(self.straight_width * 2 + 2 * math.pi * self.radius_height)


class _SVGElement:
    SVGS_PATH = os.path.join(
        os.path.dirname(os.path.realpath(__file__)), "resources", "static", "images"
    )

    def __init__(self, name: str, svg: Optional[et.Element] = None) -> None:
        if svg is None:
            svg_path = os.path.join(self.SVGS_PATH, f"{name}.svg")
            with open(svg_path, "r") as file:
                svg_str = file.read()
            self.svg = et.fromstring(svg_str)
        else:
            self.svg = svg
        self.name = name

    def __str__(self) -> str:
        string: str = et.tostring(self.svg, encoding="utf8", method="xml").decode(
            "utf8"
        )
        return string

    def __repr__(self) -> str:
        return f"_SVGElement<name={self.name}, id={id(self)}>"

    @staticmethod
    def _x_path(name: str, attr_name: Optional[str] = None) -> str:
        if attr_name is not None:
            return f".//*[@{attr_name}='{name}']"
        return f".//{name}"

    def get_sub_svg(self, name: str, attr_name: Optional[str] = None) -> "_SVGElement":
        xpath = self._x_path(name, attr_name)
        svg = self.svg.find(xpath)
        if svg is None:
            raise KeyError(f"unable to find sub svg with arguments {name}")
        return _SVGElement(name, svg)

    def get_sub_svgs(
        self, name: str, attr_name: Optional[str] = None
    ) -> List["_SVGElement"]:
        xpath = self._x_path(name, attr_name)
        svgs = self.svg.findall(xpath)
        if not svgs:
            raise KeyError(f"unable to find sub svg with arguments {name}")
        return [_SVGElement(name, svg) for svg in svgs]

    def get_svg_attr(self, tag_name: str) -> Optional[str]:
        return self.svg.get(tag_name, None)

    def set_svg_attr(self, tag_name: str, value: str) -> "_SVGElement":
        self.svg.set(tag_name, value)
        return self

    @property
    def x(self) -> float:
        value = self.get_svg_attr("x")
        if value is None:
            return 0
        return float(value)

    @x.setter
    def x(self, x: float) -> None:
        self.set_svg_attr("x", str(x))

    @property
    def y(self) -> float:
        value = self.get_svg_attr("y")
        if value is None:
            return 0
        return float(value)

    @y.setter
    def y(self, y: float) -> None:
        self.set_svg_attr("y", str(y))

    @property
    def width(self) -> float:
        value = self.get_svg_attr("width")
        if value is None:
            return 0
        return float(value)

    @width.setter
    def width(self, width: float) -> None:
        self.set_svg_attr("width", str(width))

    @property
    def height(self) -> float:
        value = self.get_svg_attr("height")
        if value is None:
            return 0
        return float(value)

    @height.setter
    def height(self, height: float) -> None:
        self.set_svg_attr("height", str(height))

    @property
    def id(self) -> Optional[str]:
        return self.get_svg_attr("id")

    @id.setter
    def id(self, id: str) -> None:
        self.set_svg_attr("id", str(id))

    @property
    def view_box(self) -> Optional[str]:
        return self.get_svg_attr("viewBox")

    @view_box.setter
    def view_box(self, view_box: str) -> None:
        self.set_svg_attr("viewBox", view_box)

    @property
    def view_box_x(self) -> Optional[float]:
        view_box = self.view_box
        if view_box is None:
            return view_box
        return float(view_box.split(" ")[0])

    @view_box_x.setter
    def view_box_x(self, view_box_x: float) -> None:
        if self.view_box is not None:
            split_view_box = self.view_box.split(" ")
            split_view_box[0] = str(view_box_x)
            view_box = " ".join(split_view_box)
            self.set_svg_attr("viewBox", view_box)

    @property
    def view_box_y(self) -> Optional[float]:
        view_box = self.view_box
        if view_box is None:
            return view_box
        return float(view_box.split(" ")[1])

    @view_box_y.setter
    def view_box_y(self, view_box_y: float) -> None:
        if self.view_box is not None:
            split_view_box = self.view_box.split(" ")
            split_view_box[1] = str(view_box_y)
            view_box = " ".join(split_view_box)
            self.set_svg_attr("viewBox", view_box)

    @property
    def view_box_width(self) -> Optional[float]:
        view_box = self.view_box
        if view_box is None:
            return view_box
        return float(view_box.split(" ")[2])

    @view_box_width.setter
    def view_box_width(self, view_box_width: float) -> None:
        if self.view_box is not None:
            split_view_box = self.view_box.split(" ")
            split_view_box[2] = str(view_box_width)
            view_box = " ".join(split_view_box)
            self.set_svg_attr("viewBox", view_box)

    @property
    def view_box_height(self) -> Optional[float]:
        view_box = self.view_box
        if view_box is None:
            return view_box
        return float(view_box.split(" ")[3])

    @view_box_height.setter
    def view_box_height(self, view_box_height: float) -> None:
        if self.view_box is not None:
            split_view_box = self.view_box.split(" ")
            split_view_box[3] = str(view_box_height)
            view_box = " ".join(split_view_box)
            self.set_svg_attr("viewBox", view_box)

    def center_x(
        self, other: Optional["_SVGElement"] = None, x: Optional[float] = None
    ) -> "_SVGElement":
        if other is not None:
            if other.view_box_width is not None:
                other_width = other.view_box_width
            else:
                other_width = other.width
            self.x = (other_width - self.width) / 2
        if x is not None:
            self.x = x - self.width / 2
        return self

    def center_y(
        self, other: Optional["_SVGElement"] = None, y: Optional[float] = None
    ) -> "_SVGElement":
        if other is not None:
            if other.view_box_height is not None:
                other_height = other.view_box_height
            else:
                other_height = other.height
            self.y = (other_height - self.height) / 2
        if y is not None:
            self.y = y - self.height / 2
        return self

    def center(
        self,
        other: Optional["_SVGElement"] = None,
        x: Optional[float] = None,
        y: Optional[float] = None,
    ) -> "_SVGElement":
        self.center_x(other, x)
        self.center_y(other, y)
        return self

    def extend(
        self, other: Union[List["_SVGElement"], List[et.Element]]
    ) -> "_SVGElement":
        for element in other:
            self.append(element)
        return self

    def append(self, other: Union["_SVGElement", et.Element]) -> "_SVGElement":
        if isinstance(other, _SVGElement):
            other = other.svg
        self.svg.append(other)
        return self

    def remove(self, other: "_SVGElement") -> "_SVGElement":
        self.svg.remove(other.svg)
        return self

    def copy(self) -> "_SVGElement":
        return copy.deepcopy(self)


class _SVGPoker:
    def __init__(
        self, num_players: int, num_hole_cards: int, num_community_cards: int
    ) -> None:
        self.num_players = num_players
        self.num_hole_cards = num_hole_cards
        self.num_community_cards = num_community_cards
        self.base_svg = self._base_svg()

    def _base_svg(self) -> "_SVGElement":
        base = _SVGElement("base")
        table = _SVGElement("table")
        player = _SVGElement("player")
        card = _SVGElement("card")
        street_commit = _SVGElement("street_commit")
        for pattern in _SVGElement("patterns").get_sub_svgs("pattern"):
            base.append(pattern)

        table.center(other=base)
        base.append(table)

        player_rectangle = _RoundedRectangle(
            table.x, table.y, table.width, table.height
        )
        player_rectangle.width += 100
        player_rectangle.height += 100
        street_commit_rectangle = _RoundedRectangle(
            table.x, table.y, table.width, table.height
        )
        street_commit_rectangle.width -= 225
        street_commit_rectangle.height -= 185

        players = self.add_players(player, card, player_rectangle)
        street_commits = self.add_street_commits(street_commit, street_commit_rectangle)
        community = self.add_community(player, card)
        community.center(other=table)
        community.x += table.x
        community.y += table.y - 40

        base.extend(players)
        base.extend(street_commits)
        base.append(community)

        return base

    @staticmethod
    def new_player(
        player: _SVGElement, label: str, card: _SVGElement, num_cards: int
    ) -> _SVGElement:
        new_player = player.copy()
        new_player.id = label
        card_width = card.width
        cards = new_player.get_sub_svg("cards", "class")
        cards.id = f"cards-{label}"

        player_background = new_player.get_sub_svg("player-background", "class")
        player_background.id = f"player-background-{label}"
        chips = new_player.get_sub_svg("chips", "class")
        chips.id = f"chips-{label}"
        chips_background = chips.get_sub_svg("chips-background", "class")
        chips_background.id = f"chips-background-{label}"
        chips_text = chips.get_sub_svg("chips-text", "class")
        chips_text.id = f"chips-text-{label}"
        for card_idx in range(num_cards):
            new_card = card.copy()
            new_card.center_x(cards)
            offset = (-card_width * num_cards / 2) + card_width * (card_idx + 0.5)
            new_card.x += offset
            new_card.id = f"card-{label}-{card_idx}"
            card_background = new_card.get_sub_svg("card-background", "class")
            card_background.id = f"card-background-{label}-{card_idx}"
            card_text = new_card.get_sub_svg("card-text", "class")
            card_text.id = f"card-text-{label}-{card_idx}"
            cards.append(new_card)
        return new_player

    def add_players(
        self,
        player: _SVGElement,
        card: _SVGElement,
        player_rectangle: _RoundedRectangle,
    ) -> List[_SVGElement]:
        players = []
        player = player.copy()
        card = card.copy()
        player.width = max(
            0 if player.width is None else player.width,
            card.width * self.num_hole_cards + 20,
        )

        player_background = player.get_sub_svg("player-background", "class")
        player_background.width = player.width - 10
        player_background.height = player_background.height - 10

        cards = player.get_sub_svg("cards", "class")
        cards.width = self.num_hole_cards * card.width
        cards.center_x(player)

        card_background = card.get_sub_svg("card-background", "class")
        card_background.set_svg_attr("fill", "url(#card-back)")

        chips = player.get_sub_svg("chips", "class")
        chips.width = player.width - 20
        chips.center_x(player)

        for player_idx in range(self.num_players):
            x, y = player_rectangle.edge(player_idx / (self.num_players))
            new_player = self.new_player(
                player, str(player_idx), card, self.num_hole_cards
            )
            new_player.center(x=round(x), y=round(y))
            players.append(new_player)
        return players

    def add_community(self, player: _SVGElement, card: _SVGElement) -> _SVGElement:
        community = player.copy()
        card = card.copy()

        community.width = card.width * (self.num_community_cards + 1) + 20
        cards = community.get_sub_svg("cards", "class")
        cards.width = community.width
        card_background = card.get_sub_svg("card-background", "class")
        card_background.set_svg_attr("fill", "url(#card-blank)")
        cards.center_x(community)

        community = self.new_player(
            community, "community", card, self.num_community_cards + 1
        )

        community_background = community.get_sub_svg("player-background", "class")
        community.remove(community_background)

        chips = community.get_sub_svg("chips", "class")
        chips.width = community.width - 60
        chips.center_x(community)
        chips.id = "pot"
        chips.get_sub_svg("chips-background", "class").id = "pot-background"
        chips.get_sub_svg("chips-text", "class").id = "pot-text"
        community.set_svg_attr("class", "community")
        card_0 = community.get_sub_svg("card-community-0", "id")
        card_0.get_sub_svg("card-background", "class").set_svg_attr(
            "fill", "url(#card-back)"
        )
        card_0.x -= 10
        return community

    def add_street_commits(
        self, street_commit: _SVGElement, street_commit_retangle: _RoundedRectangle
    ) -> List[_SVGElement]:
        street_commits = []
        for player_idx in range(self.num_players):
            x, y = street_commit_retangle.edge(player_idx / (self.num_players))
            new_street_commit = street_commit.copy()
            new_street_commit.id = f"street-commit-{player_idx}"
            street_commit_background = new_street_commit.get_sub_svg(
                "chips-background", "class"
            )
            street_commit_background.id = f"street-commit-background-{player_idx}"
            street_commit_text = new_street_commit.get_sub_svg("chips-text", "class")
            street_commit_text.id = f"street-commit-text-{player_idx}"
            button = new_street_commit.get_sub_svg("button", "class")
            button.id = f"button-{player_idx}"
            button_background = button.get_sub_svg("button-background", "class")
            button_background.id = f"button-background-{player_idx}"
            new_street_commit.center(x=round(x), y=round(y))
            street_commits.append(new_street_commit)
        return street_commits
