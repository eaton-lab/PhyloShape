#!/usr/bin/env python

"""Reconstruct ancestral shape

"""

from typing import Union, Dict, TypeVar
from pathlib import Path

from loguru import logger
import numpy as np
from scipy.optimize import minimize, basinhopping
from symengine import Symbol, lambdify, expand
# from sympy import Symbol, lambdify, expand
from symengine import log as s_log
from toytree.io.src.treeio import tree

from phyloshape.shape.src.vectors import VertexVectorMapper
from phyloshape.shape.src.shape_alignment import ShapeAlignment
from phyloshape.shape.src.vertex import Vertices
from phyloshape.phylo.src.models import *

Url = TypeVar("Url")
logger = logger.bind(name="phyloshape")


# TODO
class Tree:
    """
    A simplified tree object for ad hoc phylogenetic analysis
    """
    def __init__(self):
        pass
# temporarily use toytree instead


class PhyloShape:
    """...

    The main function call is `reconstruct_ancestral_shapes_using_ml()`

    """
    def __init__(
        self,
        tree_obj,
        shape_alignments: ShapeAlignment,
        model=None,
        vect_transform=None,
        vect_inverse_transform=None,
    ):
        """...

        Parameters
        ----------
        tree_obj: TODO can be str/path/url
            ...
        shape_alignments: ShapeAlignment
            ...shape labels must match tree labels
        """
        self.tree = tree_obj
        self.shapes = shape_alignments
        self.faces = shape_alignments.faces
        self.__map_shape_to_tip()
        # TODO: more models to think about
        self.model = model if model else Brownian()
        assert isinstance(self.model, MotionModel)
        assert (vect_transform is None) == (vect_inverse_transform is None), \
            "vect_transform and vect_inverse_transform must be used together!"
        self.vect_transform = vect_transform
        self.vect_inverse_transform = vect_inverse_transform

        self.__variables = []
        # self.vv_translator is a FaceVectorMapper, making conversion between vertices and vectors
        self.vv_translator = None
        self.loglike_form = 0
        self.negloglike_func = None  # for point estimation
        self.negloglike_func_s = None  # for scipy optimization
        self.__result = None

    def __map_shape_to_tip(self):
        # set vertices as features of tip Nodes
        for node_id in range(self.tree.ntips):
            leaf_node = self.tree[node_id]
            assert leaf_node.name in self.shapes, f"data must include all tips in the tree. Missing={leaf_node.name}"
            label, leaf_node.vertices = self.shapes[leaf_node.name]
            # logger.trace(label + str(leaf_node.vertices.coords))

    def build_vv_translator(self, mode="network-local", num_vs=20, num_vt_iter=5):
        """Assigns an initialized VertexVectorMapper to self.vv_translator

        The vv_translator ...
        """
        self.vv_translator = None
        # if self.faces is None or len(self.faces) == 0:
        # build the translator using vertices only
        # TODO use alignment information rather than using the vertices of the first sample
        if mode == "old":
            label, vertices = self.shapes[0]
            from phyloshape.shape.src.vectors import VertexVectorMapperOld
            self.vv_translator = VertexVectorMapperOld(vertices)
            logger.info(f"using {label} to construct the vector system ..")
        else:
            self.vv_translator = VertexVectorMapper(
                [vt.coords for lb, vt in self.shapes],
                mode=mode,
                num_vs=num_vs,
                num_vt_iter=num_vt_iter,
                )
        # else:
        #     self.vv_translator = FaceVectorMapper(self.faces.vertex_ids)
        len_vt = len(self.vv_translator.vh_list())
        logger.info("Vertex:Vector ({}:{}) translator built.".format(len_vt + 1, len_vt))

    def build_tip_vectors(self) -> None:
        """Assigns a .vectors attribute to each Node.

        The tip Node vector information is created by calling the
        vv_translator.to_vectors() function on the Node vertices.
        """
        if self.vect_transform is None:
            for node_id in range(self.tree.ntips):
                leaf_node = self.tree[node_id]
                leaf_node.vectors = self.vv_translator.to_vectors(leaf_node.vertices)
        else:
            vectors_list = []
            for node_id in range(self.tree.ntips):
                vectors_list.append(self.vv_translator.to_vectors(self.tree[node_id].vertices))
            # transform the original vectors to modified (e.g. PCA) ones
            for node_id, trans_vectors in enumerate(self.vect_transform(vectors_list)):
                self.tree[node_id].vectors = trans_vectors
            logger.info("Dimension {} -> {}".format(vectors_list[0].shape, self.tree[0].vectors.shape))
        logger.info("Vectors for {} tips built.".format(self.tree.ntips))

    def sym_ancestral_vectors(self):
        # n_vectors = self.shapes.n_vertices() - 1
        vectors_shape = self.tree[0].vectors.shape
        n_vals = np.prod(vectors_shape)
        for node_id in range(self.tree.ntips, self.tree.nnodes):
            ancestral_node = self.tree[node_id]
            ancestral_node.vectors = \
                ancestral_node.vectors_symbols = \
                np.array([Symbol("%s_%s" % (node_id, go_v)) for go_v in range(n_vals)]).reshape(vectors_shape)
                # np.array([[Symbol("%s_%s_%s" % (_dim, node_id, go_v))
                #            for _dim in ("x", "y", "z")]
                #           for go_v in range(n_vectors)])
        logger.info("Vectors for {} ancestral nodes symbolized.".format(self.tree.nnodes - self.tree.ntips))

    def formularize_log_like(self, log_func):
        """
        :param log_func:
             input symengine.log for maximum likelihood analysis using scipy,
             input tt.log for bayesian analysis using pymc3
        :return:
        """
        self.loglike_form = 0
        for node in self.tree.traverse():
            if not node.is_root():
                new_term = self.model.form_log_like(
                    time=node._dist, from_states=node.up.vectors, to_states=node.vectors, log_f=log_func)
                self.loglike_form += new_term
                logger.trace("Term {} -> {} {}: {}".format(node.up.idx, node.idx, node.name, new_term))
        self.__update_variables()
        logger.info("Num of variables: %i" % len(self.__variables))
        logger.info("Log-likelihood formula constructed.")
        logger.trace("Formula: {}".format(self.loglike_form))

    def functionalize_log_like(self):
        self.negloglike_func = lambdify(args=self.__variables,
                                        exprs=[-self.loglike_form])
        self.negloglike_func_s = lambda x: self.negloglike_func(*tuple(x))
        logger.trace("Log-likelihood formula functionalized.")

    def __update_variables(self):
        self.__variables = []
        self.__variables.extend(self.model.get_parameters())
        for ancestral_node_id in range(self.tree.ntips, self.tree.nnodes):
            self.__variables.extend(self.tree[ancestral_node_id].vectors_symbols.flatten().tolist())

    # def get_variables(self):
    #     return list(self.__variables)

    # TODO
    # def compute_point_negloglike(self, model_params, ancestral_shapes):
    #     assert self.loglike_form
    #     assert self.negloglike_func_s
    #     # some conversion
    #     return self.negloglike_func(converted_model_params + linearized_shapes)

    def __summarize_ml_result(self):
        # 1. assign first part of the result.x to model parameters
        model_params_signs = self.model.get_parameters()
        go_p = len(model_params_signs)
        model_params = self.__result.x[:go_p]
        logger.info(", ".join(["%s=%f" % (_s, _v) for _s, _v in zip(model_params_signs, model_params)]))
        # 2. assign second part of the result.x to ancestral shape vectors
        # n_vts = self.shapes.n_vertices() - 1
        vectors_shape = self.tree[0].vectors.shape
        n_vals = np.prod(vectors_shape)
        for anc_node_id in range(self.tree.ntips, self.tree.nnodes):
            to_p = go_p + n_vals
            self.tree[anc_node_id].vectors = np.array(self.__result.x[go_p: to_p]).reshape(vectors_shape)
            go_p = to_p

    def minimize_negloglike(self, num_proc=1):
        # constraints = # use constraints to avoid self-collision
        # verbose = False
        # other_optimization_options = {"disp": verbose, "maxiter": 5000, "ftol": 1.0e-6, "eps": 1.0e-10}
        count_run = 0
        success_runs = []
        logger.info("Searching for the best solution ..")
        while count_run < 200:
            # propose the initials as the average of the values
            vector_mean = np.average([self.tree[_n_id].vectors for _n_id in range(self.tree.ntips)], axis=0).flatten()
            len_vt = len(vector_mean)
            initials = np.random.random(len(self.__variables))
            initials[-len_vt:] = (1 - (initials[-len_vt:] - 0.5) * 0.01)
            logger.trace("initials: " + str(initials))
            #
            # result = minimize(
            #     fun=self.negloglike_func_s,
            #     x0=initials,
            #     method="L-BFGS-B",
            #     tol=1e-6,
            #     # constraints=constraints,
            #     jac=False,
            #     options=other_optimization_options)
            minimizer_kwargs = {"method": "L-BFGS-B", "options": {"maxiter": 1000}, "tol": 1e-4}
            # if num_proc == 1:
            result = basinhopping(self.negloglike_func_s,
                                  x0=initials,
                                  minimizer_kwargs=minimizer_kwargs,
                                  niter=10,
                                  seed=12345678)

            # multiprocessing not working yet
            # else:
            #     from optimparallel import minimize_parallel
            #     result = minimize_parallel(self.negloglike_func_s,
            #                                x0=initials, parallel={"max_workers": num_proc})
            if result.success:
                success_runs.append(result)
                break
                # TODO: test if more success runs are necessary
                # if len(success_runs) > 5:
                #     break
            count_run += 1
        if success_runs:
            # TODO: test if more success runs are necessary
            # result = sorted(success_runs, key=lambda x: x.fun)[0]
            result = success_runs[0]
            logger.info("Loglikelihood: %s" % result.fun)
            self.__result = result
            self.__summarize_ml_result()
        else:
            raise Exception("optimization failed!")  # TODO: find the error object from scipy

    def build_ancestral_vertices(self):
        if self.vect_inverse_transform is None:
            for anc_node_id in range(self.tree.ntips, self.tree.nnodes):
                self.tree[anc_node_id].vertices = \
                    Vertices(self.vv_translator.to_vertices(self.tree[anc_node_id].vectors))
        else:
            for anc_node_id in range(self.tree.ntips, self.tree.nnodes):
                real_vectors = self.vect_inverse_transform(self.tree[anc_node_id].vectors)
                self.tree[anc_node_id].vertices = \
                    Vertices(self.vv_translator.to_vertices(real_vectors))

    def reconstruct_ancestral_shapes_using_ml(
            self,
            mode="network-local",
            num_vs: int = 20,
            num_vt_iter: int = 5,
            num_proc: int = 1):
        """
        maximum likelihood approach
        :return:
        """
        self.build_vv_translator(mode, num_vs=num_vs, num_vt_iter=num_vt_iter)
        self.build_tip_vectors()
        self.sym_ancestral_vectors()
        self.formularize_log_like(log_func=s_log)
        self.functionalize_log_like()
        self.minimize_negloglike(num_proc=1)  # multiprocessing not working yet
        self.build_ancestral_vertices()

    def reconstruct_ancestral_shapes_using_gpa(self):
        """
        :return:
        """
        # TODO
        pass


if __name__ == "__main__":

    # EXAMPLE
    pass
