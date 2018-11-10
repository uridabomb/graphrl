import torch
from graphrl import struct2vec, graphrl


def test_s2v_grad_sanity():
    features = torch.tensor([0., 0, 0, 0])
    adjacency = torch.tensor([[0., 0, 1, 0], [0, 0, 0, 1], [1, 0, 0, 1], [0, 1, 1, 0]])
    weights = torch.tensor([[1.] * 4] * 4) * adjacency

    g = graphrl.Graph(features, adjacency, weights)

    ge = struct2vec.GraphEmbedder()
    assert ge(g)