"""Manage cannon placements."""
import json
from collections import defaultdict
from os import getcwd, path
from typing import Dict, List, Optional, Set, Tuple, Union

import numpy as np
from map_analyzer import MapData
from map_analyzer.Pather import draw_circle
from sc2.bot_ai import BotAI
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2, Point3
from sc2.unit import Unit
from scipy.signal import convolve2d

from ares import ManagerMediator
from bot.consts import (
    BLOCKING,
    DESIRABILITY_KERNEL,
    FINAL_PLACEMENT,
    INVALID_BLOCK,
    LOCATION,
    POINTS,
    SCORE,
    TYPE_ID,
    WEIGHT,
)

from .grids import modify_two_by_two


class CannonPlacement:
    """Class containing details of cannon placement."""

    def __init__(self, ai: BotAI, map_data: MapData, manager_mediator: ManagerMediator):
        """Set up the CannonPlacement tools.

        Parameters
        ----------
        ai :
            Bot Object running the game.
        map_data :
            MapData object for the map.
        """
        self.ai: BotAI = ai
        self.map_data: MapData = map_data
        self.manager_mediator: ManagerMediator = manager_mediator
        self.basic_cannon_grid = self.map_data.get_walling_grid()

        self.initial_cannon = Point2((31, 99))
        self.initial_xbound = (
            int(max([0, self.initial_cannon.x - 20])),
            int(
                min(
                    [
                        self.ai.game_info.placement_grid.data_numpy.shape[0],
                        self.initial_cannon.x + 20,
                    ]
                )
            ),
        )
        self.initial_ybound = (
            int(max([0, self.initial_cannon.y - 20])),
            int(
                min(
                    [
                        self.ai.game_info.placement_grid.data_numpy.shape[1],
                        self.initial_cannon.y + 20,
                    ]
                )
            ),
        )
        self.last_wall_component_placed: Optional[Unit] = None
        self.current_walling_path: Optional[List[Union[Point2, Tuple[int, int]]]] = None
        # self.next_building_location: Optional[Point2] = None
        self.valid_blocks: Optional[Dict[Tuple[int, int], int]] = None
        self.invalid_blocks: Optional[Dict[Tuple[int, int], int]] = None

        self.desirability_kernel = DESIRABILITY_KERNEL
        self.invalid_values = INVALID_BLOCK
        self.terrain_weight = 1
        self.blocking_building_weight = 100
        self.non_blocking_building_weight = 200

        self.recalculate_wall_path: bool = True
        self.calculate_next_pylon = True
        self.wall_start_point: Optional[Point2] = None

        self.next_building: Optional[Dict[str, Union[Point2, UnitID]]] = None

        __location__ = path.realpath(path.join(getcwd(), path.dirname(__file__)))
        with open(path.join(__location__, "hamming_weight_lookups.json"), "r") as f:
            hamming_lookup = json.load(f)
            self.hamming_lookup = {int(v): hamming_lookup[v] for v in hamming_lookup}

    def update(self) -> None:
        """Update the cannon placements."""
        # self.debug_coordinates()
        if self.calculate_next_pylon:
            self.generate_basic_cannon_grid([self.initial_cannon.position])
            target_region = self.map_data.in_region_p(self.initial_cannon)
            basic_grid = self.map_data.get_pyastar_grid()
            self.valid_blocks, self.invalid_blocks = self.perform_convolutions(
                x_bound=self.initial_xbound,
                y_bound=self.initial_ybound,
                terrain_height={
                    self.ai.get_terrain_height(point)
                    for point in target_region.points
                    if self.ai.in_pathing_grid(point)
                },
                pathing_grid=basic_grid,
            )
            if not self.current_walling_path or self.recalculate_wall_path:
                if possible_path := self.find_wall_path(self.initial_cannon):
                    # if self.current_walling_path:
                    #     if len(possible_path) < len(self.current_walling_path):
                    #         self.current_walling_path = possible_path
                    # else:
                    self.current_walling_path = possible_path
        # if (
        #     self.ai.structures(UnitID.FORGE).amount
        #     and not self.ai.structures(UnitID.PHOTONCANNON).amount
        #     and self.ai.state.psionic_matrix.covers(self.initial_cannon)
        # ):
        #     self.next_building = {
        #         LOCATION: self.initial_cannon,
        #         TYPE_ID: UnitID.PHOTONCANNON,
        #     }
        # el
        if self.current_walling_path:
            if self.wall_is_finished(self.initial_cannon):
                # TODO: cycle cannons so we can keep things going
                self.next_building = {
                    LOCATION: self.initial_cannon,
                    TYPE_ID: UnitID.PHOTONCANNON,
                    FINAL_PLACEMENT: False,
                }
                return
            if next_building_location := self.get_next_walling_position():
                self.next_building = {
                    FINAL_PLACEMENT: self.placement_would_complete_wall(
                        next_building_location
                    ),
                    LOCATION: next_building_location,
                    TYPE_ID: UnitID.PYLON,
                }

    def generate_convolution_grid(
        self,
        x_bound: Tuple[int, int],
        y_bound: Tuple[int, int],
        terrain_height: Set[int],
        pathing_grid: np.ndarray,
    ) -> np.ndarray:
        """Generate the grids to convolve based on pathing and placement."""
        x_min, x_max = x_bound
        y_min, y_max = y_bound
        convolution_grid = np.ones((x_max - x_min + 1, y_max - y_min + 1))

        for i in range(*x_bound):
            for j in range(*y_bound):
                if (
                    pathing_grid[i][j] == 1
                    and self.ai.game_info.placement_grid.data_numpy[j][i] == 1
                    and self.ai.game_info.terrain_height.data_numpy[j][i]
                    in terrain_height
                ):
                    convolution_grid[i - x_min][j - y_min] = 0

        # ensure the initial cannon isn't considered
        if (x_min < self.initial_cannon[0] < x_max) and (
            y_min < self.initial_cannon[1] < y_max
        ):
            for i in [-1, 0]:
                for j in [-1, 0]:
                    convolution_grid[
                        (
                            self.initial_cannon[0] + i - x_min,
                            self.initial_cannon[1] + j - y_min,
                        )
                    ] = 1

        return convolution_grid

    def get_hamming_value(
        self,
        point: Union[Point2, Tuple[int, int]],
        dictionaries: List[Dict],
        remove_corners: bool = True,
    ) -> int:
        """Given a point and the possible dictionaries with convolution scores it could
        be in, find its Hamming weight.
        """
        for curr_dict in dictionaries:
            if point in curr_dict:
                if not remove_corners:
                    return self.hamming_lookup[curr_dict[point]]
                else:
                    # the corners are A, D, I, and J which correspond to 100100001001
                    # val & 0b100100001001 returns only the corners that are used in val
                    # so the subtraction removes the corners before looking up the
                    # Hamming weight
                    val = int(curr_dict[point])
                    return self.hamming_lookup[val - (val & 0b100100001001)]
        return -999

    def perform_convolutions(
        self,
        x_bound: Tuple[int, int],
        y_bound: Tuple[int, int],
        terrain_height: Set[int],
        pathing_grid: np.ndarray,
    ) -> Tuple[Dict, Dict]:
        """Convolve grids and return the dictionaries."""
        # get the grid and our boundaries
        grid = self.generate_convolution_grid(
            x_bound, y_bound, terrain_height, pathing_grid
        )
        x_min, _x_max = x_bound
        y_min, _y_max = y_bound

        # perform convolution and identify valid blocks
        placements = convolve2d(grid, DESIRABILITY_KERNEL, mode="valid")

        valid_blocks: Dict[Tuple[int, int], int] = {}
        valid_non_blocking_positions: Dict[Tuple[int, int], int] = {}
        for x in range(placements.shape[0]):
            for y in range(placements.shape[1]):
                point = (x + x_min + 2, y + y_min + 2)
                score = placements[x][y]
                if score >= 4096:
                    # invalid placement
                    continue
                elif score in INVALID_BLOCK:
                    # valid placement, but it doesn't block
                    valid_non_blocking_positions[point] = score
                else:
                    valid_blocks[point] = score
        return valid_blocks, valid_non_blocking_positions

    def find_wall_path(self, cannon_placement: Point2) -> Optional[List[Point2]]:
        """Given a location to place a cannon, find the path we want to wall."""
        cannon_grid = self.basic_cannon_grid.copy()

        if not self.wall_start_point:
            # see if we can find a starting position
            self.wall_start_point = self.calculate_start_point(
                self.initial_cannon, cannon_grid, {(89, 138)}
            )

        for pos in self.valid_blocks:
            modify_two_by_two(cannon_grid, pos, self.blocking_building_weight)

        for pos in self.invalid_blocks:
            modify_two_by_two(cannon_grid, pos, self.non_blocking_building_weight)

        # don't use the cannon as part of the wall
        modify_two_by_two(cannon_grid, cannon_placement, np.inf)

        if not self.wall_start_point:
            # try again after modifying the cannon grid
            self.wall_start_point = self.calculate_start_point(
                self.initial_cannon, cannon_grid, {(89, 138)}
            )
        if self.wall_start_point:
            # only run pathfinding if we have a start point
            return self.map_data.clockwise_pathfind(
                start=self.wall_start_point,
                goal=self.wall_start_point,
                origin=cannon_placement,
                grid=cannon_grid,
            )
        return None

    def evaluate_walling_positions(
        self,
        valid_blocks: Dict[Tuple[int, int], int],
        invalid_blocks: Dict[Tuple[int, int], int],
        path: Set[Point2],
    ):
        """Given the points in a path and positions we can use to block them, score them
        based on usability."""
        scores = {}
        blocking = True
        for d in [valid_blocks, invalid_blocks]:
            for pos in d.keys():
                score = 0
                tiles = []
                for i in [-1, 0]:
                    for j in [-1, 0]:
                        tile = (pos[0] + i, pos[1] + j)
                        if tile in path:
                            score += 1
                            tiles.append(tile)
                if score >= 1:
                    scores[pos] = {
                        SCORE: score,
                        POINTS: tiles,
                        BLOCKING: blocking,
                        WEIGHT: self.hamming_lookup[d[pos]],
                    }
            blocking = not blocking
        return scores

    def get_next_walling_position(self) -> Optional[Point2]:
        """Figure out where we want to put the next building."""
        pylon_usage = self.evaluate_walling_positions(
            self.valid_blocks, self.invalid_blocks, set(self.current_walling_path)
        )
        pylon_points = []
        for v in pylon_usage.values():
            pylon_points.extend(v[POINTS])
        scores = defaultdict(list)
        # best_weight = 0
        for point in pylon_usage:
            scores[pylon_usage[point][SCORE]].append(point)
        if not scores or max(scores) == 0:
            return None
        possible_positions = np.array(scores[max(scores)])
        cannon_array = np.array(self.initial_cannon)
        # pylon_pos = possible_positions[
        #     np.argmin(np.sum((possible_positions - cannon_array) ** 2, axis=1))
        # ]
        pylon_pos = possible_positions[
            np.argmin(
                np.sum(
                    (
                        possible_positions
                        - self.manager_mediator.get_enemy_ramp.bottom_center
                    )
                    ** 2,
                    axis=1,
                )
            )
        ]
        return Point2(pylon_pos)

    def wall_is_finished(
        self, cannon_placement: Point2, grid_override: Optional[np.array] = None
    ) -> bool:
        """Determine if the wall is completed."""
        if wall_path := self.map_data.clockwise_pathfind(
            start=self.wall_start_point,
            goal=self.wall_start_point,
            origin=cannon_placement,
            grid=self.basic_cannon_grid if grid_override is None else grid_override,
        ):
            if len(wall_path) < 40:
                return True
        return False

    def placement_would_complete_wall(self, placement: Point2, _size: int = 2) -> bool:
        """Check whether this placement will complete the wall.

        Used to make sure our Probe is on the correct side of the wall.

        Warning
        -------
        Only supports size=2

        Parameters
        ----------
        placement : Point2
            Where the building is being placed
        _size : int
            Side length of the building's footprint.

        Returns
        -------
        bool :
            Whether this placement will complete the wall.

        """
        fake_grid = self.basic_cannon_grid.copy()
        modify_two_by_two(fake_grid, placement, np.inf)
        return self.wall_is_finished(self.initial_cannon, grid_override=fake_grid)

    def calculate_start_point(
        self,
        cannon_placement: Point2,
        grid: np.ndarray,
        blacklist: Optional[Set[Union[Point2, Tuple[int, int]]]] = None,
    ) -> Optional[Point2]:
        """Given the cannon we want to wall, find the start/end point for our path."""
        start = None
        if not blacklist:
            blacklist = set()
        for pos in blacklist:
            grid[pos] = np.inf
        point = (int(cannon_placement[0]), int(cannon_placement[1]))
        disk = tuple(draw_circle(point, 8, shape=grid.shape))
        target_weight_cond = np.logical_and(
            np.abs(grid[disk])
            # < max(self.blocking_building_weight + 1, self.terrain_weight),
            < self.terrain_weight + 1,
            grid[disk] < np.inf,
        )
        if np.any(target_weight_cond):
            possible_points = np.column_stack(
                (disk[0][target_weight_cond], disk[1][target_weight_cond])
            )

            closest_point_index = np.argmin(
                np.sum((possible_points - point) ** 2, axis=1)
            )
            start = tuple(possible_points[closest_point_index])
        return start

    def generate_basic_cannon_grid(
        self, cannon_positions: Optional[List[Point2]] = None
    ) -> None:
        """Set the up the inverted pathing grid."""
        basic_grid = self.map_data.get_walling_grid().copy()
        self.basic_cannon_grid = np.zeros(basic_grid.shape, dtype=np.float32)
        self.basic_cannon_grid[np.where(basic_grid != np.inf)] = np.inf
        self.basic_cannon_grid[np.where(basic_grid == np.inf)] = 1
        if cannon_positions:
            for cannon in cannon_positions:
                modify_two_by_two(self.basic_cannon_grid, cannon, np.inf)

    def get_high_ground_point_near_cannon(
        self, cannon_position: Point2
    ) -> Optional[Point2]:
        """Find some high ground near a cannon.

        Parameters
        ----------
        cannon_position : Point2
            The cannon position we want to be near

        Returns
        -------
        Optional[Point2] :
            The high ground point.

        """
        target_height = self.ai.get_terrain_height(self.ai.enemy_start_locations[0])
        grid = self.ai.game_info.terrain_height.data_numpy
        point = (int(cannon_position[0]), int(cannon_position[1]))
        disk = tuple(draw_circle(point, 7, shape=grid.shape))
        target_weight_cond = np.logical_and(
            abs(np.abs(grid[disk]) - target_height) < 11,
            grid[disk] < np.inf,
        )
        if np.any(target_weight_cond):
            blocks, non_blocks = self.perform_convolutions(
                x_bound=(int(cannon_position.x - 7), int(cannon_position.x + 7)),
                y_bound=(int(cannon_position.y - 7), int(cannon_position.y + 7)),
                terrain_height={target_height},
                pathing_grid=self.map_data.get_pyastar_grid(),
            )
            possible_points = np.column_stack(
                (disk[0][target_weight_cond], disk[1][target_weight_cond])
            )
            for point in blocks:
                if point in possible_points:
                    return Point2(point)
            for point in non_blocks:
                if point in possible_points:
                    return Point2(point)
        return None

    def debug_coordinates(self):
        """Draw coordinates on the screen."""
        for i in range(
            int(self.initial_cannon.x - 15), int(self.initial_cannon.x + 15)
        ):
            for j in range(
                int(self.initial_cannon.y - 15), int(self.initial_cannon.y + 15)
            ):
                point = Point2((i, j))
                height = self.ai.get_terrain_z_height(point)
                p_min = Point3((point.x, point.y, height + 0.1))
                p_max = Point3((point.x + 1, point.y + 1, height + 0.1))
                self.ai.client.debug_box_out(p_min, p_max, Point3((0, 0, 127)))
                if height >= 9:
                    self.ai.client.debug_text_world(
                        f"x={i}\ny={j}",
                        Point3((p_min.x, p_min.y + 0.75, p_min.z)),
                        (127, 0, 255),
                    )
