import copy
import random
import collections
import torch
import torch.nn.functional as F


class Problem:
    def cumul_reward(self, solution, fr, to):
        return self.cost(solution.subsolution_at_step(to)) - self.cost(solution.subsolution_at_step(fr))

    def terminate(self, solution):
        raise NotImplementedError

    def cost(self, solution):
        raise NotImplementedError

class MVCProblem(Problem):
    def terminate(self, solution):
        adjacency = solution.adjacency().clone()
        for sel in solution:
            adjacency[sel, :].fill_(0)
            adjacency[:, sel].fill_(0)
        return not torch.any(torch.eq(adjacency, 1))

    def cost(self, solution):
        return -float(len(solution))


class Graph:
    def __init__(self, adjacency, weights):
        assert adjacency.size(0) == adjacency.size(1)
        assert adjacency.size() == weights.size()
        self._adjacency = adjacency
        self._weights = weights
        self.adjcache = {}
        self.wtcache = {}

    def adjacency(self, pad=None):
        if pad in self.adjcache:
            return self.adjcache[pad]

        if pad:
            assert pad >= len(self)
            ret = F.pad(self._adjacency, (0, pad - len(self), 0, pad - len(self)))
        else:
            ret = self._adjacency
        self.adjcache[pad] = ret
        return ret

    def weights(self, pad=None):
        if pad in self.wtcache:
            return self.wtcache[pad]

        if pad:
            assert pad >= len(self)
            ret = F.pad(self._weights, (0, pad - len(self), 0, pad - len(self)))
        else:
            ret = self._weights
        self.wtcache[pad] = ret
        return ret

    def __len__(self):
        return self._adjacency.size(0)

    def __eq__(self, val):
        return torch.equal(self._adjacency, val._adjacency) and torch.equal(self._weights, val._weights)


class Solution:
    def __init__(self, graph: Graph, solution=None,
                 n_steps=None, featurevec=None, device='cpu'):
        graph._adjacency = graph._adjacency.to(device)
        graph._weights = graph._weights.to(device)
        self.graph = graph
        self.featurevec = featurevec
        self.solution = [] if solution is None else solution
        self.nsteps = n_steps
        self.dev = device

        if featurevec is None:
            self.featurevec = torch.zeros(len(graph), device=device)
            for i in self.solution:
                self.featurevec[i] = 1.

    def add_node(self, nodeidx):
        assert 0 <= nodeidx and nodeidx < len(self.graph)
        self._resolve_view()
        assert self.featurevec[nodeidx] == 0
        self.nsteps = len(self.solution)

        self.solution.append(nodeidx)
        self.featurevec[nodeidx] = len(self.solution)
        return Solution(self.graph, self.solution, None, self.featurevec, device=self.dev)

    def adjacency(self, pad=None):
        return self.graph.adjacency(pad=pad)

    def weights(self, pad=None):
        return self.graph.weights(pad=pad)

    def features(self, pad=None):
        if self.nsteps is None:
            f = self.featurevec != 0
        else:
            f = (self.featurevec <= self.nsteps) * (self.featurevec != 0)

        if pad:
            assert pad >= len(self.graph)
            return F.pad(f.float(), (0, pad - len(self.graph)))
        else:
            return f

    def nodes_included(self):
        if self.nsteps is not None:
            return self.solution[:self.nsteps]
        return self.solution

    def pick_random_node(self):
        self._resolve_view()
        num_nodes = len(self.graph) - len(self.solution)
        if num_nodes <= 0:
            raise IndexError()
        pos = random.randrange(0, num_nodes)
        for i, e in enumerate(self.featurevec):
            if not e:
                if pos == 0:
                    return i
                else:
                    pos -= 1
        assert False

    def pick_node(self, embedder, epsilon):
        if len(self) >= len(self.graph):
            raise IndexError('Cannot pick new node! Solution is already as long as graph!')
        if random.random() < epsilon:
            return self.pick_random_node()
        else:
            qs, embs = embedder([self])
            qs = qs[0][:len(self.graph)]
            # mask out illegal choices
            qs += torch.tensor(-9999999999., device=qs.device) * \
                self.features().float()
            vals, inds = qs.max(0)
            assert vals > -999999999
            return int(inds)

    def __contains__(self, node):
        return bool(self.featurevec[node])

    def __len__(self):
        if self.nsteps is not None:
            return self.nsteps
        else:
            return len(self.solution)

    def __iter__(self):
        return SolutionIter(self)

    def __str__(self):
        return str(self.solution[:self.nsteps] if self.nsteps else self.solution)

    def _resolve_view(self):
        if self.nsteps is not None:
            # resolve view by making copy of data
            self.featurevec = self.featurevec.clone()

            for i in self.solution[self.nsteps:]:
                self.featurevec[i] = 0
            self.solution = self.solution[:self.nsteps]
            self.nsteps = None

    def __getitem__(self, key):
        if self.nsteps is not None:
            if key >= self.nsteps:
                raise IndexError
        return self.solution[key]

    def __eq__(self, val):
        try:
            if len(self) != len(val):
                return False

            if self.graph != val.graph:
                return False

            if self.solution is val.solution and self.nsteps == val.nsteps:
                return True

            for i in range(len(self)):
                if self[i] != val[i]:
                    return False

            return True
        except TypeError:
            return False

    def subsolution_at_step(self, step):
        return Solution(self.graph, self.solution, step, self.featurevec, device=self.dev)


class SolutionIter:
    def __init__(self, solution):
        self.solution = solution
        self.i = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.i < len(self.solution):
            r = self.solution.solution[self.i]
            self.i += 1
            return r
        else:
            raise StopIteration()

    def next(self):
        return self.__next__()


class ReplayMemory:
    def __init__(self, capacity):
        self.capacity = capacity
        self.arr = []
        self.index = 0

    def add(self, solution):
        if len(self.arr) < self.capacity:
            self.arr.append(solution)
            self.index = len(self.arr) % self.capacity
        else:
            self.arr[self.index] = solution
            self.index = (self.index + 1) % self.capacity

    def sample(self, batchsize):
        assert batchsize <= len(self.arr)

        return random.sample(self.arr, batchsize)

    def __len__(self):
        return len(self.arr)

    def __iter__(self):
        return self.arr.__iter__()

    def __str__(self):
        return self.arr.__str__()

    def __eq__(self, val):
        return self.arr == val.arr
