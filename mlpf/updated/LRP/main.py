from glob import glob
import sys, os
import os.path as osp
import pickle, math, time, numba, tqdm
import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import accuracy_score, confusion_matrix
import matplotlib, mplhep
matplotlib.use("Agg")
import matplotlib.pyplot as plt

#Check if the GPU configuration has been provided
import torch
use_gpu = torch.cuda.device_count()>0
multi_gpu = torch.cuda.device_count()>1

try:
    if not ("CUDA_VISIBLE_DEVICES" in os.environ):
        import setGPU
        if multi_gpu:
            print('Will use multi_gpu..')
            print("Let's use", torch.cuda.device_count(), "GPUs!")
        else:
            print('Will use single_gpu..')
except Exception as e:
    print("Could not import setGPU, running CPU-only")

#define the global base device
if use_gpu:
    device = torch.device('cuda:0')
else:
    device = torch.device('cpu')

import torch_geometric
import torch.nn as nn
import torch.nn.functional as F
import torch_geometric.transforms as T
from torch.nn import Sequential as Seq, Linear as Lin, ReLU
from torch_scatter import scatter_mean
from torch_geometric.nn.inits import reset
from torch_geometric.data import Data, DataLoader, DataListLoader, Batch
from torch_geometric.nn import GravNetConv
from torch.utils.data import random_split
import torch_cluster

sys.path.insert(1, '../')
sys.path.insert(1, '../../../plotting/')
sys.path.insert(1, '../../../mlpf/plotting/')
import args
from args import parse_args
from graph_data_delphes import PFGraphDataset, one_hot_embedding
from data_preprocessing import data_to_loader_ttbar, data_to_loader_qcd
import evaluate
from evaluate import make_plots, Evaluate
from plot_utils import plot_confusion_matrix
from model_LRP import PFNet7

from LRP import LRP
from model_io import model_io

import networkx as nx
from torch_geometric.utils.convert import to_networkx
from tabulate import tabulate

#Ignore divide by 0 errors
np.seterr(divide='ignore', invalid='ignore')

#Get a unique directory name for the model
def get_model_fname(dataset, model, n_train, n_epochs, lr, target_type, batch_size, task, title):
    model_name = type(model).__name__
    model_params = sum(p.numel() for p in model.parameters())
    import hashlib
    model_cfghash = hashlib.blake2b(repr(model).encode()).hexdigest()[:10]
    model_user = os.environ['USER']

    model_fname = '{}_{}_ntrain_{}_nepochs_{}_batch_size_{}_lr_{}_{}'.format(
        model_name,
        target_type,
        n_train,
        n_epochs,
        batch_size,
        lr,
        task,
        title)
    return model_fname

def map_classid_to_classname(id):
    if id==0:
        return 'null'
    if id==1:
        return 'charged hadron'
    if id==2:
        return 'neutral hadron'
    if id==3:
        return 'photon'
    if id==4:
        return 'electron'
    if id==5:
        return 'muon'

if __name__ == "__main__":

    # args = parse_args()

    # the next part initializes some args values (to run the script not from terminal)
    class objectview(object):
        def __init__(self, d):
            self.__dict__ = d

    args = objectview({'train': False, 'n_train': 1, 'n_valid': 1, 'n_test': 2, 'n_epochs': 2, 'patience': 100, 'hidden_dim':256, 'input_encoding': 12, 'encoding_dim': 125,
    'batch_size': 1, 'model': 'PFNet7', 'target': 'gen', 'dataset': '../../../test_tmp_delphes/data/pythia8_ttbar', 'dataset_qcd': '../../../test_tmp_delphes/data/pythia8_qcd',
    'outpath': '../../../prp/models/LRP/', 'optimizer': 'adam', 'lr': 0.001, 'alpha': 1, 'dropout': 0,
    'space_dim': 4, 'propagate_dimensions': 22,'nearest': 16, 'overwrite': True,
    'load': True, 'load_epoch': 19, 'load_model': 'DataParallel_gen_ntrain_400_nepochs_100_batch_size_4_lr_0.0001_clf_noskip',
    'evaluate': False, 'evaluate_on_cpu': False, 'classification_only': True, 'nn1': False, 'conv2': False, 'nn3': False, 'title': '',
    'explain': True})

    # define the dataset (assumes the data exists as .pt files in "processed")
    print('Processing the data..')
    full_dataset_ttbar = PFGraphDataset(args.dataset)
    full_dataset_qcd = PFGraphDataset(args.dataset_qcd)

    # constructs a loader from the data to iterate over batches
    print('Constructing data loaders..')
    train_loader, valid_loader = data_to_loader_ttbar(full_dataset_ttbar, args.n_train, args.n_valid, batch_size=args.batch_size)
    test_loader = data_to_loader_qcd(full_dataset_qcd, args.n_test, batch_size=args.batch_size)

    # element parameters
    input_dim = 12

    #one-hot particle ID and momentum
    output_dim_id = 6
    output_dim_p4 = 6

    patience = args.patience

    model_classes = {"PFNet7": PFNet7}

    model_class = model_classes[args.model]
    model_kwargs = {'input_dim': input_dim,
                    'hidden_dim': args.hidden_dim,
                    'input_encoding': args.input_encoding,
                    'encoding_dim': args.encoding_dim,
                    'output_dim_id': output_dim_id,
                    'output_dim_p4': output_dim_p4,
                    'dropout_rate': args.dropout,
                    'space_dim': args.space_dim,
                    'propagate_dimensions': args.propagate_dimensions,
                    'nearest': args.nearest,
                    'target': args.target,
                    'nn1': args.nn1,
                    'conv2': args.conv2,
                    'nn3': args.nn3}

    print('Loading a previously trained model..')
    model = model_class(**model_kwargs)
    outpath = args.outpath + args.load_model
    PATH = outpath + '/epoch_' + str(args.load_epoch) + '_weights.pth'

    state_dict = torch.load(PATH, map_location=device)

    if "DataParallel" in args.load_model:
        state_dict = torch.load(PATH, map_location=device)
        from collections import OrderedDict
        new_state_dict = OrderedDict()
        for k, v in state_dict.items():
            name = k[7:] # remove module.
            new_state_dict[name] = v
            # print('name is:', name)
        state_dict=new_state_dict

    model.load_state_dict(state_dict)

    if args.explain:
        model.train()
        print(model)

        results = []
        signal =torch.tensor([1,0,0,0,0,0],dtype=torch.float32).to(device)

        # create some hooks to retrieve intermediate activations
        activation = {}
        hooks={}

        def get_activation(name):
            def hook(model, input, output):
                activation[name] = input[0]
            return hook

        param=dict()
        for i, parameter in enumerate(model.parameters()):
            param[i]=parameter
            print(parameter.shape)

        for name, module in model.named_modules():
            if (type(module)==nn.Linear) or (type(module)==nn.LeakyReLU):
                hooks[name] = module.register_forward_hook(get_activation("." + name))

        for i, batch in enumerate(train_loader):
            t0 = time.time()

            if multi_gpu:
                X = batch
            else:
                X = batch.to(device)

            if i==0:
                # could be defined better
                # I run at least one forward pass to get the activation to use them in defining the LRP layers
                cand_ids_one_hot, cand_p4, target_ids_one_hot, target_p4, edge_index, edge_weight, after_message, before_message = model(X)
                model=model_io(model,state_dict,dict(),activation)
                explainer=LRP(model)

            else:
                cand_ids_one_hot, cand_p4, target_ids_one_hot, target_p4, edge_index, edge_weight, after_message, before_message = model.model(X)

            to_explain={"A":activation,"inputs":dict(x=X.x,
                                                batch=X.batch),"y":target_ids_one_hot,"R":dict(), "pred":cand_ids_one_hot,
                                                "edge_index":edge_index, "edge_weight":edge_weight, "after_message":after_message, "before_message":before_message}

            model.set_dest(to_explain["A"])

            R, big_list = explainer.explain(to_explain,save=False,return_result=True, signal=signal)

            results.append(R)

            # if i==0:
            #     print('LRP layers are:', to_explain['A'].keys())
            #     print(results[i][0])         # 0 indicates the first layer (i.e. relevance scores of the input)
            #
            # if True:
            #     res = torch.abs(results[i][0])
            # else:
            #     res = torch.cat((res,torch.abs(results[i][0])), dim=0)

            # ------------------------------------
            cand_ids = cand_ids_one_hot.argmax(axis=1)
            target_ids = target_ids_one_hot.argmax(axis=1)

            for classs in range(6):
                list0, list1, list2, list3, list4, list5 = [], [], [], [], [], []
                dist0, dist1, dist2, dist3, dist4, dist5 = [], [], [], [], [], []

                for i,id in enumerate(target_ids):
                    R_cat_feat_cat_pred = torch.cat([big_list[classs][i], X.x, cand_ids_one_hot, torch.arange(start=0, end=X.x.shape[0], step=1, dtype=int).reshape(-1,1)], dim=1)
                    if id==0:
                        list0.append(R_cat_feat_cat_pred)
                        dist0.append(i)
                    if id==1:
                        list1.append(R_cat_feat_cat_pred)
                        dist1.append(i)
                    if id==2:
                        list2.append(R_cat_feat_cat_pred)
                        dist2.append(i)
                    if id==3:
                        list3.append(R_cat_feat_cat_pred)
                        dist3.append(i)
                    if id==4:
                        list4.append(R_cat_feat_cat_pred)
                        dist4.append(i)
                    if id==5:
                        list5.append(R_cat_feat_cat_pred)
                        dist5.append(i)

                list=[list0,list1,list2,list3,list4,list5]
                dist=[dist0,dist1,dist2,dist3,dist4,dist5]

                for pid in range(5):
                    print('pid', pid)
                    for j in range(len(list[pid])): # iterating over the nodes in a graph
                        # to keep non-zero rows
                        non_empty_mask = list[pid][j][:,:12].abs().sum(dim=1).bool()
                        harvest=list[pid][j][non_empty_mask,:]
                        pos=dist[pid][j]

                        def make_list(t):
                            l=[]
                            for elem in t:
                                if elem==1:
                                    l.append('cluster')
                                if elem==2:
                                    l.append('track')
                            return l

                        node_types = make_list(harvest[:,12])

                        features = ["type", " pt", "eta",
                                   "sphi", "cphi", "E/P", "eta_outer", "sphi_outer", "cphi_outer", "charge", "is_gen_muon", "is_gen_electron"]

                        fig, ax = plt.subplots()
                        fig.tight_layout()
                        if pid==0:
                            ax.set_title('Heatmap for the "'+map_classid_to_classname(classs)+'" prediction of a true null')
                        if pid==1:
                            ax.set_title('Heatmap for the "'+map_classid_to_classname(classs)+'" prediction of a true charged hadron')
                        if pid==2:
                            ax.set_title('Heatmap for the "'+map_classid_to_classname(classs)+'" prediction of a true neutral hadron')
                        if pid==3:
                            ax.set_title('Heatmap for the "'+map_classid_to_classname(classs)+'" prediction of a true photon')
                        if pid==4:
                            ax.set_title('Heatmap for the "'+map_classid_to_classname(classs)+'" prediction of a true electron')
                        if pid==5:
                            ax.set_title('Heatmap for the "'+map_classid_to_classname(classs)+'" prediction of a true muon')
                        ax.set_xticks(np.arange(len(features)))
                        ax.set_yticks(np.arange(len(node_types)))
                        for col in range(len(features)):
                            for row in range(len(node_types)):
                                text = ax.text(col, row, round(harvest[row,12+col].item(),2),
                                               ha="center", va="center", color="w")
                        # ... and label them with the respective list entries
                        ax.set_xticklabels(features)
                        ax.set_yticklabels(node_types)
                        plt.xlabel("\noutput prediction:{R} \nposition of node is row # {harvest}".format(R=[round(num,2) for num in harvest[j, 24:30].tolist()], harvest=((harvest[:,30] == pos).nonzero(as_tuple=True)[0].item()+1)))
                        plt.imshow(torch.abs(harvest[:,:12]*10**7).detach().numpy(), interpolation="nearest", cmap='copper')
                        plt.colorbar()
                        fig.set_size_inches(11, 16)
                        plt.savefig("class"+str(classs)+"/pid"+str(pid)+"/sample"+str(j)+".jpg")
                        plt.close(fig)

                        if j==3:
                            break

            break

    # evaluate the model
    if args.evaluate:
        if args.evaluate_on_cpu:
            device = "cpu"

        model = model.to(device)
        model.eval()
        Evaluate(model, test_loader, outpath, args.target, device, args.load_epoch)

## -----------------------------------------------------------
# # to retrieve a stored variable in pkl file
# import pickle
# with open('../../test_tmp_delphes/experiments/PFNet7_gen_ntrain_2_nepochs_3_batch_size_3_lr_0.0001/confusion_matrix_plots/cmT_normed_epoch_0.pkl', 'rb') as f:  # Python 3: open(..., 'rb')
#     a = pickle.load(f)

#
# R_cat_feat_cat_pred.shape
# disstt
# harvest[:,30]
# (harvest[:,30] == pos).nonzero(as_tuple=True)[0].item()
#
#
#
#         for j in range(len(list[pid])): # draw 5 figures of each output
#             # to keep non-zero rows
#             # one_hot=
#             non_empty_mask = list[pid][j][:,:12].abs().sum(dim=1).bool()
#             harvest=list[pid][j][non_empty_mask,:]
#             pos=dist[pid][j]
#             R = R_cat_feat_cat_pred[non_empty_mask,:]
#
#             def make_list(t):
#                 l=[]
#                 for elem in t:
#                     if elem==1:
#                         l.append('cluster')
#                     if elem==2:
#                         l.append('track')
#                 return l
#
#             node_types = make_list(harvest[:,12])
#
#             features = ["type", " pt", "eta",
#                        "sphi", "cphi", "E/P", "eta_outer", "sphi_outer", "cphi_outer", "charge", "is_gen_muon", "is_gen_electron"]
#
#             fig, ax = plt.subplots()
#             fig.tight_layout()
#             if pid==0:
#                 ax.set_title("Heatmap for a null classified as a " + map_classid_to_classname(classs))
#             if pid==1:
#                 ax.set_title("Heatmap for a charged hadron classified as a " + map_classid_to_classname(classs))
#             if pid==2:
#                 ax.set_title("Heatmap for a neutral hadron classified as a " + map_classid_to_classname(classs))
#             if pid==3:
#                 ax.set_title("Heatmap for a photon classified as a " + map_classid_to_classname(classs))
#             if pid==4:
#                 ax.set_title("Heatmap for a electron classified as a " + map_classid_to_classname(classs))
#             if pid==5:
#                 ax.set_title("Heatmap for a muon classified as a " + map_classid_to_classname(classs))
#             ax.set_xticks(np.arange(len(features)))
#             ax.set_yticks(np.arange(len(node_types)))
#             for col in range(len(features)):
#                 for row in range(len(node_types)):
#                     text = ax.text(col, row, round(harvest[row,12+col].item(),2),
#                                    ha="center", va="center", color="w")
#             # ... and label them with the respective list entries
#             ax.set_xticklabels(features)
#             ax.set_yticklabels(node_types)
#             plt.xlabel("\noutput prediction:{R} \nposition of node is row # {harvest}".format(R_cat_feat_cat_pred=[round(num,2) for num in R[j, 24:30].tolist()], harvest=((harvest[:,30] == pos).nonzero(as_tuple=True)[0].item()+1)))
#             plt.imshow(torch.abs(harvest[:,:12]*10**7).detach().numpy(), interpolation="nearest", cmap='copper')
#             plt.colorbar()
#             fig.set_size_inches(11, 16)
#             plt.savefig("class"+str(classs)+"/pid"+str(pid)+"/sample"+str(j)+".jpg")
#             plt.close(fig)
