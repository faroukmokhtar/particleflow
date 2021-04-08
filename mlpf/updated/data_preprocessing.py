import numpy as np
import torch
from torch_geometric.data import Data, DataLoader, DataListLoader, Batch

use_gpu = torch.cuda.device_count()>0
multi_gpu = torch.cuda.device_count()>1
multi_gpu=0

#multi_gpu=0
#define the global base device
if use_gpu:
    device = torch.device('cuda:0')
else:
    device = torch.device('cpu')

# define a function that casts the ttbar dataset into a dataloader for efficient NN training
def data_to_loader_ttbar(full_dataset, n_train, n_valid, batch_size):

    train_dataset = torch.utils.data.Subset(full_dataset, np.arange(start=0, stop=n_train))
    valid_dataset = torch.utils.data.Subset(full_dataset, np.arange(start=n_train, stop=n_train+n_valid))

    # preprocessing the train_dataset in a good format for passing correct batches of events to the GNN
    train_data=[]
    for i in range(len(train_dataset)):
        train_data = train_data + train_dataset[i]

    # preprocessing the valid_dataset in a good format for passing correct batches of events to the GNN
    valid_data=[]
    for i in range(len(valid_dataset)):
        valid_data = valid_data + valid_dataset[i]

    #hack for multi-gpu training
    if not multi_gpu:
        def collate(items):
            l = sum([items], [])
            return Batch.from_data_list(l)
    else:
        def collate(items):
            l = sum([items], [])
            return l

    train_loader = DataListLoader(train_data, batch_size, pin_memory=True, shuffle=True, drop_last=True)
    train_loader.collate_fn = collate
    valid_loader = DataListLoader(valid_data, batch_size, pin_memory=True, shuffle=False, drop_last=True)
    valid_loader.collate_fn = collate

    return train_loader, valid_loader

# define a function that casts the dataset into a dataloader for efficient NN training
def data_to_loader_qcd(full_dataset, n_test, batch_size):

    test_dataset = torch.utils.data.Subset(full_dataset, np.arange(start=0, stop=n_test))

    # preprocessing the test_dataset in a good format for passing correct batches of events to the GNN
    test_data=[]
    for i in range(len(test_dataset)):
        test_data = test_data + test_dataset[i]

    #hack for multi-gpu training
    if not multi_gpu:
        def collate(items):
            l = sum([items], [])
            return Batch.from_data_list(l)
    else:
        def collate(items):
            l = sum([items], [])
            return l

    test_loader = DataListLoader(test_data, batch_size, pin_memory=True, shuffle=False, drop_last=True)
    test_loader.collate_fn = collate

    return test_loader


#
# from graph_data_delphes import PFGraphDataset, one_hot_embedding
# # the next part initializes some args values (to run the script not from terminal)
# class objectview(object):
#     def __init__(self, d):
#         self.__dict__ = d
#
# args = objectview({'train': True, 'n_train': 3, 'n_valid': 1, 'n_test': 2, 'n_epochs': 1, 'patience': 100, 'hidden_dim':32, 'encoding_dim': 256,
# 'batch_size': 24, 'model': 'PFNet7', 'target': 'gen', 'dataset': '../../test_tmp_delphes/data/pythia8_ttbar', 'dataset_qcd': '../../test_tmp_delphes/data/pythia8_qcd',
# 'outpath': '../../test_tmp_delphes/experiments/', 'activation': 'leaky_relu', 'optimizer': 'adam', 'lr': 1e-4, 'l1': 1, 'l2': 0.001, 'l3': 1, 'dropout': 0.5,
# 'radius': 0.1, 'convlayer': 'gravnet-knn', 'convlayer2': 'none', 'space_dim': 2, 'nearest': 3, 'overwrite': True,
# 'input_encoding': 0, 'load': False, 'load_epoch': 0, 'load_model': 'PFNet7_cand_ntrain_3_nepochs_1', 'evaluate': True, 'evaluate_on_cpu': True})
#
# full_dataset = PFGraphDataset(args.dataset_qcd)
#
#
# test_dataset = torch.utils.data.Subset(full_dataset, np.arange(start=0, stop=args.n_test))
#
# # preprocessing the test_dataset in a good format for passing correct batches of events to the GNN
# test_data=[]
# for i in range(len(test_dataset)):
#     test_data = test_data + test_dataset[i]
#
#
# def collate(items):
#     l = sum([items], [])
#     return Batch.from_data_list(l)
#
# test_loader = DataListLoader(test_data, 4, pin_memory=True, shuffle=False)
# test_loader.collate_fn = collate
#
#
# for batch in test_loader:
#     break
#
# batch
#
# batch.ycand
# #
#
# test_dataset = torch.utils.data.Subset(full_dataset, np.arange(start=0, stop=args.n_test))
#
# # preprocessing the test_dataset in a good format for passing correct batches of events to the GNN
# test_data=[]
# for i in range(len(test_dataset)):
#     test_data = test_data + test_dataset[i]
#
#
# def collate(items):
#     l = sum([items], [])
#     return l
#
# test_loader_m = DataListLoader(test_data, 4, pin_memory=True, shuffle=False)
# test_loader_m.collate_fn = collate
#
#
#
# for batch_m in test_loader_m:
#     break
#
#
# # batch_m
# #
# #
# tot = full_dataset[0]+full_dataset[1]+full_dataset[2]
# len(tot)
#
#     train_dataset = torch.utils.data.Subset(full_dataset, np.arange(start=0, stop=args.n_train))
#     print("train_dataset", len(train_dataset))
#
#     #hack for multi-gpu training
#     if not multi_gpu:
#         def collate(items):
#             l = sum(items, [])
#             return Batch.from_data_list(l)
#     else:
#         def collate(items):
#             l = sum(items, [])
#             return l
#
#     train_loader = DataListLoader(train_dataset, batch_size=1, pin_memory=False, shuffle=False)
#     train_loader.collate_fn = collate
#     val_loader = DataListLoader(val_dataset, batch_size=args.batch_size, pin_memory=False, shuffle=False)
#     val_loader.collate_fn = collate
#
#
#
#      for batch in train_loader:
#          break
#
#     batch
#
#
#
#
#
# len(batch_m)
# len(batch)
#
#     batch_m[0]
#     batch[0]
