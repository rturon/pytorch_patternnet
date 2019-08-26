import torch
import numpy as np
import torch.nn as nn
import patterns

class PatternConv2d(nn.Module):

    def __init__(self, conv_layer):
        super(PatternConv2d, self).__init__()

        if conv_layer.dilation != (1,1):

            def dilation_mask(kernel_size, dilation):
    
                mask = torch.zeros(kernel_size[0], kernel_size[1], 
                                kernel_size[2]+(dilation[0]-1)*(kernel_size[2]-1),
                                kernel_size[3]+(dilation[1]-1)*(kernel_size[3]-1))
                
                locs_x = np.arange(0, mask.shape[2], dilation[0])
                locs_y = np.arange(0, mask.shape[3], dilation[1])
                inds_x, inds_y = np.meshgrid(locs_x, locs_y)
                
                mask[:,:,inds_x, inds_y] = 1
                
                return mask

            self.dil_mask = lambda ks: dilation_mask(ks, conv_layer.dilation)

        
        self.forward_layer = conv_layer  # kernels size of forward layer: 
                                         # self.forward_layer.kernel_size
        padding_f = np.array(conv_layer.padding)
        ks = np.array(self.forward_layer.kernel_size) 
        padding_b = tuple(-padding_f + ks - 1)
        self.backward_layer =  nn.Conv2d(
            conv_layer.out_channels,
            conv_layer.in_channels,
            ks,
            stride=conv_layer.stride,
            padding=padding_b,
            bias=False,
        )
    
        self.statistics = None
        self.patterns = None

    def forward(self, input):
        ''' perform forward computations of forward_layer, return not only new
            output, but also output without bias, if the forward layer has a 
            bias parameter.
        '''
        
        def expand_bias(bias, size):
            new_tensor = torch.zeros((size))
            for i in range(bias.shape[0]):
                new_tensor[:, i, :, :] = bias[i]

            return new_tensor

        
        output = self.forward_layer(input)
        # what if the layer does not have a bias?
        if self.forward_layer.bias is None:
            return output
        bias = expand_bias(self.forward_layer.bias.data, output.data.shape)
        output_wo_bias = output - bias

        return output, output_wo_bias


    def backward(self, input, normalize_output=True):
        ''' compute a backward step (for signal computation).
        '''
        output = self.backward_layer(input)
        # if the dilation is not none the output has to be 
        # dilated to the original input size
        if self.forward_layer.dilation != (1,1):
            output_mask = self.dil_mask(output.shape)
            output_dilated = torch.zeros(output_mask.shape)
            output_dilated[output_mask == 1] = torch.flatten(output)
            output = output_dilated
        if normalize_output:
            # rescale output to be between -1 and 1
            absmax = torch.abs(output.data).max()
            if absmax > 0.000001:
                output.data /= absmax
            output.data[output.data > 1] = 1
            output.data[output.data < -1] = -1

        


        return output


    def compute_statistics(self, input, output, output_wo_bias=None):
        ''' compute statistics for this layer given the input, output and 
            output without bias. Initialize statistics if none there yet,
            otherwise update statistics with new values.

            If the forward layer does not use a bias term, then the output
            without bias, i.e. the layer's output, is in output and there is 
            no tensor in output_wo_bias.
        '''
        kernel_size = self.forward_layer.kernel_size
        if self.forward_layer.dilation != (1,1):
            dilation = self.forward_layer.dilation
            kernel_size = tuple((kernel_size[0]+(dilation[0]-1)*(kernel_size[0]-1),
                                kernel_size[1]+(dilation[1]-1)*(kernel_size[1]-1)))

        if output_wo_bias is None:
            inp_dense, out_dense = patterns._conv_maps_to_dense(input, output,
                                                                kernel_size)
            if self.forward_layer.dilation != (1,1):
                inp_mask = torch.flatten(self.dil_mask(self.forward_layer.weight.shape)[0])
                inp_dense = inp_dense[:, inp_mask==1]

            if self.statistics is None:
                self.statistics = patterns.compute_statistics(inp_dense, 
                                                              out_dense, 
                                                              out_dense)
            else:
                self.statistics = patterns.update_statistics(inp_dense,
                                                             out_dense,
                                                             out_dense,
                                                             self.statistics)

        else:
            inp_dense, out_wo_bias_dense = patterns._conv_maps_to_dense(input, 
                                            output_wo_bias,
                                            kernel_size)
            _, out_dense = patterns._conv_maps_to_dense(input, output,
                                        kernel_size)
            if self.forward_layer.dilation != (1,1):
                inp_mask = torch.flatten(self.dil_mask(self.forward_layer.weight.shape)[0])
                inp_dense = inp_dense[:, inp_mask==1]
 
            if self.statistics is None:
                self.statistics = patterns.compute_statistics(inp_dense, 
                                                            out_wo_bias_dense, 
                                                            out_dense)
            else:
                self.statistics = patterns.update_statistics(inp_dense,
                                                             out_wo_bias_dense,
                                                             out_dense,
                                                             self.statistics)
        

    def compute_patterns(self):
        ''' Compute patterns from the computed statistics. 
        '''
        kernel = self.forward_layer.weight.data
        self.patterns = patterns.compute_patterns_conv(self.statistics, 
                                                       kernel)


    def set_patterns(self, pattern_type='relu'):
        ''' Sets the computed patterns as the kernel of the backward layer.
            pattern_type can be 'relu' or 'linear'
        '''
        if pattern_type == 'relu':
            self.backward_layer.parameters().__next__().data = self.patterns['A_plus']
        elif pattern_type == 'linear':
            self.backward_layer.parameters().__next__().data = self.patterns['A_linear']


class PatternLinear(nn.Module):

    def __init__(self, linear_layer):
        super(PatternLinear, self).__init__()

        self.forward_layer = linear_layer 

        self.backward_layer = nn.Linear(linear_layer.out_features, 
                                        linear_layer.in_features, 
                                        bias=False)

        self.statistics = None
        self.patterns = None

    def forward(self, input):
        ''' perform forward computations of forward_layer, return not only new
            output, but also output without bias, if the forward layer has a 
            bias parameter.
        '''

        def expand_bias(bias, size):
            new_tensor = torch.zeros((size))
            for i in range(bias.shape[0]):
                new_tensor[:, i] = bias[i]

            return new_tensor

        output = self.forward_layer(input)
        # TODO: what if the layer does not have a bias?
        if self.forward_layer.bias is None:
            return output
        bias = expand_bias(self.forward_layer.bias.data, output.data.shape)
        output_wo_bias = output - bias

        return output, output_wo_bias


    def backward(self, input, normalize_output=True):
        ''' compute a backward step (for signal computation).
        '''
        output = self.backward_layer(input)
        if normalize_output:
            # rescale output to be between -1 and 1
            absmax = torch.abs(output.data).max()
            if absmax > 0.000001:
                output.data /= absmax
            output.data[output.data > 1] = 1
            output.data[output.data < -1] = -1

        return output


    def compute_statistics(self, input, output, output_wo_bias=None):
        ''' compute statistics for this layer given the input, output and 
            output without bias. Initialize statistics if none there yet,
            otherwise update statistics with new values.

            If the forward layer does not use a bias term, then the output
            without bias, i.e. the layer's output, is in output and there is 
            no tensor in output_wo_bias.
        '''
        if output_wo_bias is None:
            if self.statistics is None:
                self.statistics = patterns.compute_statistics(input, 
                                                              output, 
                                                              output)
            else:
                self.statistics = patterns.update_statistics(input,
                                                             output,
                                                             output,
                                                             self.statistics)

        else:
            if self.statistics is None:
                self.statistics = patterns.compute_statistics(input, 
                                                            output_wo_bias, 
                                                            output)
            else:
                self.statistics = patterns.update_statistics(input,
                                                             output_wo_bias,
                                                             output,
                                                             self.statistics)
        

    def compute_patterns(self):
        ''' Compute patterns from the computed statistics. 
        '''
        w = self.forward_layer.weight.data
        self.patterns = patterns.compute_patterns_linear(self.statistics, w)


    def set_patterns(self, pattern_type='relu'):
        ''' Sets the computed patterns as the kernel of the backward layer.
            pattern_type can be 'relu' or 'linear'
        '''
        if pattern_type == 'relu':
            # self.backward_layer.weight.data = self.patterns['A_plus'].permute(1,0)
            self.backward_layer.parameters().__next__().data = self.patterns['A_plus'].permute(1,0)
        elif pattern_type == 'linear':
            self.backward_layer.parameters().__next__().data = self.patterns['A_linear'].permute(1,0)


class PatternReLU(nn.Module):

    def __init__(self):
        super(PatternReLU, self).__init__()

        self.forward_layer = nn.ReLU()

    def forward(self, input):
        indices = input <= 0
        output = self.forward_layer(input)

        return output, indices

    def backward(self, input, indices):
        # copy the input
        input = input.clone().detach()
        input[indices] = 0

        return input


class PatternMaxPool2d(nn.Module):

    def __init__(self, pool_layer):
        super(PatternMaxPool2d, self).__init__()

        # create a new pooling layer to use a different instance with 
        # return_indices=True without changing the original layer's
        # settings
        self.forward_layer = nn.MaxPool2d(pool_layer.kernel_size,
                                          pool_layer.stride,
                                          pool_layer.padding,
                                          pool_layer.dilation,
                                          return_indices=True)
        self.backward_layer = nn.MaxUnpool2d(pool_layer.kernel_size, 
                                             pool_layer.stride, 
                                             pool_layer.padding)

    def forward(self, input):
        return self.forward_layer(input)

    def backward(self, input, switches):
        return self.backward_layer(input, switches)
    