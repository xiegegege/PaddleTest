import numpy as np
import paddle


class LayerCase(paddle.nn.Layer):
    """
    case名称: AvgPool1D_3
    api简介: 1维平均池化
    """

    def __init__(self):
        super(LayerCase, self).__init__()
        self.func = paddle.nn.AvgPool1D(kernel_size=2, stride=1, padding=0, ceil_mode=True, )

    def forward(self, data, ):
        """
        forward
        """

        paddle.seed(33)
        np.random.seed(33)
        out = self.func(data, )
        return out



def create_inputspec(): 
    inputspec = ( 
        paddle.static.InputSpec(shape=(2, 3, 8), dtype=paddle.float32, stop_gradient=False), 
    )
    return inputspec

def create_tensor_inputs():
    """
    paddle tensor
    """
    inputs = (paddle.to_tensor(-10 + (10 - -10) * np.random.random([2, 3, 8]).astype('float32'), dtype='float32', stop_gradient=False), )
    return inputs


def create_numpy_inputs():
    """
    numpy array
    """
    inputs = (-10 + (10 - -10) * np.random.random([2, 3, 8]).astype('float32'), )
    return inputs

