#!/usr/bin/env python

"""Core PhyloShape class object of the phyloshape package.

"""

from loguru import logger
import numpy as np
from phyloshape.utils import ID_TYPE, COORD_TYPE, RGB_TYPE
from phyloshape.shape.src.vertex import Vertices
from numpy.typing import ArrayLike
from typing import Union, List, Generator


class Faces:
    def __init__(self,
                 # TODO how to specify the dimension of the array/list
                 vertex_ids: Union[ArrayLike, List, None] = None,
                 vertices: Vertices = None,
                 texture_ids: Union[ArrayLike, List, None] = None,
                 texture_anchor_percent_coords: Union[ArrayLike, List, None] = None,
                 texture_image_data: ArrayLike = None):
        self.vertex_ids = np.array([], dtype=ID_TYPE) if vertex_ids is None else np.array(vertex_ids, dtype=ID_TYPE)
        self.__vertices = Vertices() if vertices is None else vertices
        self.texture_ids = np.array([], dtype=ID_TYPE) if texture_ids is None else np.array(texture_ids, dtype=ID_TYPE)
        self.texture_anchor_percent_coords = np.array([], dtype=COORD_TYPE) if texture_anchor_percent_coords is None \
            else np.array(texture_anchor_percent_coords, dtype=COORD_TYPE)
        self.texture_image_data = np.array([], dtype=RGB_TYPE) if texture_image_data is None else texture_image_data
        if texture_anchor_percent_coords is None and texture_image_data is None:
            self.texture_anchor_coords = None
        else:
            self.texture_anchor_coords = self.texture_anchor_percent_coords * self.texture_image_data.shape[:2]

    def __getitem__(self, item):
        return self.vertex_ids[item]

    # def __iter__(self):
    #     for vertex_id in self.vertex_ids:
    #         yield vertex_id

    def get_vertex_coords(self, face_id):
        return self.__vertices[self.vertex_ids[face_id]]

    def get_texture_coords(self, face_id):
        return self.texture_anchor_coords[self.texture_ids[face_id]]

    def iter_vertex_coords(self):
        for vertex_id in self.vertex_ids:
            yield self.__vertices[vertex_id]

    def iter_texture_coords(self):
        for texture_id in self.texture_ids:
            yield self.texture_anchor_coords[texture_id]

    def iter_coords(self, coord_type: str = "vertex"):
        """
        Returns a generator that each time generates the tri-coordinates (coord_type==vertex) or
        bi-coordinates (coord_type==texture) of the three points of a face.

        :param coord_type: vertex (default) or texture
        :return: Generator[ArrayLike[ArrayLike, ArrayLike, ArrayLike]]
        """
        if coord_type == "vertex":
            for vertex_id in self.vertex_ids:
                yield self.__vertices[vertex_id]
        elif coord_type == "texture":
            for texture_id in self.texture_ids:
                yield self.texture_anchor_coords[texture_id]
        else:
            raise ValueError(coord_type + " is not a valid input! coord_type must be either vertex or texture!")

