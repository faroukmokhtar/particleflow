import torch
import torch.nn as nn
from torch.nn import Sequential as Seq,Linear,ReLU,BatchNorm1d
from torch_scatter import scatter_mean
import numpy as np
import json
import model_io
from torch_geometric.utils import to_scipy_sparse_matrix
import scipy
import pickle, math, time
import _pickle as cPickle
from sys import getsizeof

from torch_geometric.data import Data
import networkx as nx
from torch_geometric.utils.convert import to_networkx

use_gpu = torch.cuda.device_count()>0
multi_gpu = torch.cuda.device_count()>1

#define the global base device
if use_gpu:
    device = torch.device('cuda:0')
else:
    device = torch.device('cpu')

class LRP:
    EPSILON=1e-9

    def __init__(self,model:model_io):
        self.model=model

    def register_model(model:model_io):
        self.model=model

    """
    LRP rules
    """

    # this rule is wrong.. it is just here because it is much quicker for experimentation and gives the correct dimensions needed for debugging
    @staticmethod
    def easy_rule(layer,input,R,index,output_layer,activation_layer, print_statement):
        EPSILON=1e-9

        if activation_layer:
            w = torch.eye(input.shape[1])
        else:
            w = layer.weight.detach()

        if output_layer: # for the output layer
            T, W, r = [], [], []

            for i in range(R.shape[1]):
                T.append(R[:,i].reshape(-1,1))
                W.append(w[i,:].reshape(1,-1))
                I = torch.ones_like(R[:,i]).reshape(-1,1)

                Numerator = (input*torch.matmul(T[i],W[i]))
                Denominator = (input*torch.matmul(I,W[i])).sum(axis=1)

                Denominator = Denominator.reshape(-1,1).expand(Denominator.size()[0],Numerator.size()[1])
                r.append(torch.abs(Numerator / (Denominator+EPSILON*torch.sign(Denominator))))

                print('- Finished computing R-scores for output neuron #: ', i+1)
            print(f'- Completed layer: {layer}')
            return r
        else:
            for i in range(len(R)):
                I = torch.ones_like(R[i])

                Numerator = (input*torch.matmul(R[i],w))
                Denominator = (input*torch.matmul(I,w)).sum(axis=1)

                Denominator = Denominator.reshape(-1,1).expand(Denominator.size()[0],Numerator.size()[1])
                R[i]=(torch.abs(Numerator / (Denominator+EPSILON*torch.sign(Denominator))))

                print('- Finished computing R-scores for output neuron #: ', i+1)
            print(f'- Completed layer: {layer}')
            return R


    @staticmethod
    def eps_rule_before_gravnet(layer, input, R, index, output_layer, activation_layer, print_statement):

        if activation_layer:
            w = torch.eye(input.shape[1])
        else:
            w = layer.weight
            b = layer.bias

        wt = torch.transpose(w.detach(),0,1)

        if output_layer:
            R_list = [None]*R.shape[1]
            Wt = [None]*R.shape[1]
            for output_node in range(R.shape[1]):
                R_list[output_node] = (R[:,output_node].reshape(-1,1).clone())
                Wt[output_node] = (wt[:,output_node].reshape(-1,1))
        else:
            R_list = R
            Wt = [wt]*len(R_list)

        R_previous=[None]*len(R_list)
        for output_node in range(len(R_list)):
            # rep stands for repeated/expanded
            a_rep = input.reshape(input.shape[0],input.shape[1],1).expand(-1,-1,R_list[output_node].shape[1])
            wt_rep = Wt[output_node].reshape(1,Wt[output_node].shape[0],Wt[output_node].shape[1]).expand(input.shape[0],-1,-1)

            H = a_rep*wt_rep
            deno = H.sum(axis=1).reshape(H.sum(axis=1).shape[0],1,H.sum(axis=1).shape[1]).expand(-1,input.shape[1],-1)

            G = H/deno

            R_previous[output_node] = (torch.matmul(G, R_list[output_node].reshape(R_list[output_node].shape[0],R_list[output_node].shape[1],1).float()))
            R_previous[output_node] = R_previous[output_node].reshape(R_previous[output_node].shape[0], R_previous[output_node].shape[1])

            if print_statement:
                print('- Finished computing R-scores for output neuron #: ', output_node+1)

        if print_statement:
            print(f'- Completed layer: {layer}')
            if (torch.allclose(R_previous[output_node].sum(axis=1), R_list[output_node].sum(axis=1))):
                print('- R score is conserved up to relative tolerance 1e-5')
            elif (torch.allclose(R_previous[output_node].sum(axis=1), R_list[output_node].sum(axis=1), rtol=1e-4)):
                print('- R score is conserved up to relative tolerance 1e-4')
            elif (torch.allclose(R_previous[output_node].sum(axis=1), R_list[output_node].sum(axis=1), rtol=1e-3)):
                print('- R score is conserved up to relative tolerance 1e-3')
            elif (torch.allclose(R_previous[output_node].sum(axis=1), R_list[output_node].sum(axis=1), rtol=1e-2)):
                print('- R score is conserved up to relative tolerance 1e-2')
            elif (torch.allclose(R_previous[output_node].sum(axis=1), R_list[output_node].sum(axis=1), rtol=1e-1)):
                print('- R score is conserved up to relative tolerance 1e-1')
        return R_previous

    @staticmethod
    def eps_rule_after_gravnet(layer, input, R, index, activation_layer):

        if activation_layer:
            w = torch.eye(input.shape[1])
        else:
            w = layer.weight
            b = layer.bias

        wt = torch.transpose(w.detach(),0,1)

        Wt = [wt]*len(R)

        R_previous=[None]*len(R)
        for output_node in range(len(R)):
        # rep stands for repeated
            a_rep = input.reshape(1,input.shape[0],input.shape[1],1).expand(R[output_node].shape[0],-1,-1,R[output_node].shape[2])
            wt_rep = Wt[output_node].reshape(1,1,Wt[output_node].shape[0],Wt[output_node].shape[1]).expand(R[output_node].shape[0],input.shape[0],-1,-1)

            H = a_rep*wt_rep

            deno = H.sum(axis=2).reshape(H.sum(axis=2).shape[0],H.sum(axis=2).shape[1],1,H.sum(axis=2).shape[2]).expand(-1,-1,input.shape[1],-1)

            G = H/deno

            R_previous[output_node] = torch.matmul(G, R[output_node].reshape(R[output_node].shape[0],R[output_node].shape[1],R[output_node].shape[2],1))
            R_previous[output_node] = R_previous[output_node].reshape(R_previous[output_node].shape[0], R_previous[output_node].shape[1],R_previous[output_node].shape[2])

            print('- Finished computing R-scores for all nodes for output neuron #: ', output_node+1)

        print(f'- Completed layer: {layer}')

        if (torch.allclose(R_previous[output_node].sum(axis=2), R[output_node].sum(axis=2))):
            print('- R score is conserved up to relative tolerance 1e-5')
        elif (torch.allclose(R_previous[output_node].sum(axis=2), R[output_node].sum(axis=2), rtol=1e-4)):
            print('- R score is conserved up to relative tolerance 1e-4')
        elif (torch.allclose(R_previous[output_node].sum(axis=2), R[output_node].sum(axis=2), rtol=1e-3)):
            print('- R score is conserved up to relative tolerance 1e-3')
        elif (torch.allclose(R_previous[output_node].sum(axis=2), R[output_node].sum(axis=2), rtol=1e-2)):
            print('- R score is conserved up to relative tolerance 1e-2')
        elif (torch.allclose(R_previous[output_node].sum(axis=2), R[output_node].sum(axis=2), rtol=1e-1)):
            print('- R score is conserved up to relative tolerance 1e-1')
        print(R_previous[output_node].requires_grad)
        return R_previous

    # @staticmethod
    # def message_passing_rule_1(layer, input, R, big_list, edge_index, edge_weight, after_message, before_message, index):
    #
    #     big_list = [[None]*len(R)]*R[0].shape[0]
    #
    #     for node_i in range(R[0].shape[0]):
    #         for output_node in range(len(R)):
    #             big_list[node_i][output_node] = torch.zeros(R[output_node].shape[0],R[output_node].shape[1])
    #             big_list[node_i][output_node][node_i] = R[output_node][node_i]
    #
    #     print('- Completed layer: Message Passing')
    #
    #     return big_list
    #

    @staticmethod
    def message_passing_rule_2(layer, input, R, big_list, edge_index, edge_weight, after_message, before_message, index):

        big_list = [None]*len(R)
        for output_node in range(len(R)):
            big_list[output_node] = torch.zeros(R[output_node].shape[0],R[output_node].shape[0],R[output_node].shape[1])

            for node_i in range(R[output_node].shape[0]):
                big_list[output_node][node_i][node_i] = R[output_node][node_i]

            print('- Finished computing R-scores for for all nodes for output neuron # : ', output_node+1)

        print('- Completed layer: Message Passing')

        if (torch.allclose(big_list[output_node].sum(axis=1).sum(axis=1), R[output_node].sum(axis=1))):
            print('- R score is conserved up to relative tolerance 1e-5')
        elif (torch.allclose(big_list[output_node].sum(axis=1).sum(axis=1), R[output_node].sum(axis=1), rtol=1e-4)):
            print('- R score is conserved up to relative tolerance 1e-4')
        elif (torch.allclose(big_list[output_node].sum(axis=1).sum(axis=1), R[output_node].sum(axis=1), rtol=1e-3)):
            print('- R score is conserved up to relative tolerance 1e-3')
        elif (torch.allclose(big_list[output_node].sum(axis=1).sum(axis=1), R[output_node].sum(axis=1), rtol=1e-2)):
            print('- R score is conserved up to relative tolerance 1e-2')
        elif (torch.allclose(big_list[output_node].sum(axis=1).sum(axis=1), R[output_node].sum(axis=1), rtol=1e-1)):
            print('- R score is conserved up to relative tolerance 1e-1')

        return big_list

    """
    explanation functions
    """

    def explain(self,
                to_explain:dict,
                save:bool=True,
                save_to:str="./relevance.pt",
                sort_nodes_by:int=0,
                signal=torch.tensor([1,0,0,0,0,0],dtype=torch.float32).to(device),
                return_result:bool=False):

        start_index=self.model.n_layers                  ##########################
        print('Total number of layers (including activation layers):', start_index)

        ### loop over each single layer
        big_list=[]
        for index in range(start_index+1, 1,-1):
            print(f"Explaining layer {1+start_index+1-index}/{start_index+1-1}")
            if index==start_index+1:
                R, big_list  = self.explain_single_layer(to_explain["pred"].detach(), to_explain, big_list, start_index+1, index)
            else:
                R, big_list  = self.explain_single_layer(R, to_explain, big_list, start_index+1, index)

            if len(big_list)==0:
                with open(to_explain["outpath"]+'/'+to_explain["load_model"]+f'/R_score_layer{index}.pkl', 'wb') as f:
                    cPickle.dump(R, f, protocol=4)
            else:
                with open(to_explain["outpath"]+'/'+to_explain["load_model"]+f'/R_score_layer{index}.pkl', 'wb') as f:
                    cPickle.dump(big_list, f, protocol=4)

        print("Finished explaining all layers.")

    def explain_single_layer(self, R, to_explain, big_list, output_layer_index, index=None, name=None):
        # preparing variables required for computing LRP
        layer = self.model.get_layer(index=index,name=name)

        if name is None:
            name = self.model.index2name(index)
        if index is None:
            index = self.model.name2index(name)

        input=to_explain['A'][name].detach()

        if index==output_layer_index:
            output_layer_bool = True
        else:
            output_layer_bool = False

        # it works out of the box that the conv1.lin_s layer which we don't care about is in the same place of the message passing.. so we can just replace its action
        if 'conv1.lin_s' in str(name):
            big_list = self.message_passing_rule_2(layer, input, R, big_list, to_explain["edge_index"], to_explain["edge_weight"], to_explain["after_message"], to_explain["before_message"], index)
            return R, big_list

        # if you haven't hit the message passing step yet
        if len(big_list)==0:
            if 'Linear' in str(layer):
                R = self.eps_rule_before_gravnet(layer, input, R, index, output_layer_bool, activation_layer=False, print_statement=True)
            elif 'LeakyReLU' or 'ELU' in str(layer):
                R = self.eps_rule_before_gravnet(layer, input, R, index, output_layer_bool, activation_layer=True, print_statement=True)
        else:
            # # this way assumes you used message_passing_rule_1
            # # in this way: big_list is a list of length 5k (nodes) that contains a list of length 6 (output_neurons) that contains tensors (5k,x) which are the heatmap of R-scores
            # for node_i in range(len(big_list)):
            #     if 'Linear' in str(layer):
            #         big_list[node_i] = self.eps_rule_before_gravnet(layer, input, big_list[node_i], index, output_layer_bool, activation_layer=False, print_statement=False)
            #     elif 'LeakyReLU' or 'ELU' in str(layer):
            #         big_list[node_i] =  self.eps_rule_before_gravnet(layer, input, big_list[node_i], index, output_layer_bool, activation_layer=True, print_statement=False)
            #     print(f'Done with node {node_i+1}/{len(big_list)}')

            # this way assumes you used message_passing_rule_2
            # in this way: big_list is a list of length 6 (output_neurons) of a big tensor of size (5k,5k,x) which contains the heatmap of R-scores of all nodes at once (more parallelizable, but more memory)
            if 'Linear' in str(layer):
                big_list = self.eps_rule_after_gravnet(layer, input, big_list, index, activation_layer=False)
            elif 'LeakyReLU' or 'ELU' in str(layer):
                big_list =  self.eps_rule_after_gravnet(layer, input, big_list, index, activation_layer=True)

        return R, big_list

def copy_tensor(tensor,dtype=torch.float32):
    """
    create a deep copy of the provided tensor,
    outputs the copy with specified dtype
    """

    return tensor.clone().detach().requires_grad_(True).to(device)


##-----------------------------------------------------------------------------
#


# # big_list is a list of length 5k
# # each element is another list of length 6
# # each element of that second list is a tensor of shape (5k,20)
#
# # this function basically takes the 6 tensors and updates them in some way
# # initially this big_list is initialized to have values (not None)
#
# for i in range(len(big_list)): # looping over 5k
#     big_list[i] = self.eps_rule_before_gravnet(layer, input, big_list[i], index, output_layer_bool, activation_layer=False)
