import numpy as np
import paddle


class LayerCase(paddle.nn.Layer):
    """
    case名称: conv1d_7
    api简介: 1维卷积
    """

    def __init__(self):
        super(LayerCase, self).__init__()

    def forward(self, x, ):
        """
        forward
        """

        paddle.seed(33)
        np.random.seed(33)
        out = paddle.nn.functional.conv1d(x,  weight=paddle.to_tensor(-1 + (1 - -1) * np.random.random([2, 3, 3]).astype('float32'), dtype='float32', stop_gradient=False), bias=paddle.to_tensor(-1 + (1 - -1) * np.random.random([2]).astype('float32'), dtype='float32', stop_gradient=False), stride=1, padding=0, data_format='NLC', )
        return out



def create_inputspec(): 
    inputspec = ( 
        paddle.static.InputSpec(shape=(2, 4, 3), dtype=paddle.float32, stop_gradient=False), 
    )
    return inputspec

def create_tensor_inputs():
    """
    paddle tensor
    """
    inputs = (paddle.to_tensor(-1 + (1 - -1) * np.random.random([2, 4, 3]).astype('float32'), dtype='float32', stop_gradient=False), )
    return inputs


def create_numpy_inputs():
    """
    numpy array
    """
    inputs = (-1 + (1 - -1) * np.random.random([2, 4, 3]).astype('float32'), )
    return inputs

