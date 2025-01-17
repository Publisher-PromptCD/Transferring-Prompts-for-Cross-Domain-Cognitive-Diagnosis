# coding: utf-8
import logging
import time
from sklearn.model_selection import train_test_split
from ours.cross_school.MIRT import MIRT
import torch
from torch.utils.data import TensorDataset, DataLoader
import pandas as pd
import numpy as np
import argparse
#----------------------------------------------------------------------
parser = argparse.ArgumentParser(description='Description of your program')
parser.add_argument('--rate', type=float, default=0.2, help='The ratio for splitting cold start training exercises')
parser.add_argument('--pp_dim', type=int, default=10, help='The dimension of prompts')
parser.add_argument('--batch_size', type=int, default=256, help='Batch size for training')
parser.add_argument('--latent_dim', type=int, default=10, help='Description of latent_dim parameter')
parser.add_argument('--model_file', type=str, default="source_model/cross_school/mirt/temp.pth", help='')
parser.add_argument('--target_model_file', type=str, default="target_model/cross_school/mirt/temp.pth", help='')
parser.add_argument('--if_source_train', type=int, default=0, choices=[0, 1], help='Whether to train on the source domain (0 for no, 1 for yes)')
parser.add_argument('--if_target_migration', type=int, default=1, choices=[0, 1, 2], help='(0 for Origin, 1 for ours, 2 for ParTrans)')
parser.add_argument('--folder', type=str, default='../data/B+C+D+A/', help='Folder path for data')
parser.add_argument('--source', type=str, default='schoolB,schoolC,schoolD,schoolA', help='Names of source schools separated by comma')
parser.add_argument('--target', type=str, default='schoolA', help='Name of target school')
args = parser.parse_args()

rate = args.rate
pp_dim = args.pp_dim
batch_size = args.batch_size
latent_dim = args.latent_dim
model_file = args.model_file
if_source_train = args.if_source_train
if_target_migration = args.if_target_migration
folder = args.folder
source = args.source
target = args.target
target_model_file = args.target_model_file
#----------------------------------------------------------------------
Source_data = {}
for source_name in source.split(','):
    Source_data[source_name] = pd.read_csv(f"{folder}/{source_name}.csv")

Target = pd.read_csv(f"{folder}/{target}.csv")
item = pd.read_csv(f"{folder}/item.csv")
#-----------------------------------------------------------------------

Source_df = pd.concat(list(Source_data.values()), ignore_index=True)
Source_train, Source_test = train_test_split(Source_df, test_size=0.2, random_state=42)
Source_train.reset_index(inplace=True, drop=True)
Source_test.reset_index(inplace=True, drop=True)

exer_set = Target["item_id"].drop_duplicates()
rand_stu = exer_set.sample(frac=rate, random_state=2024)
Target_fine_tuning = Target[Target["item_id"].isin(rand_stu)]
Target_test = Target[~Target["item_id"].isin(rand_stu)]
Target_fine_tuning.reset_index(inplace=True, drop=True)
Target_test.reset_index(inplace=True, drop=True)

Target_train, Target_val = train_test_split(Target_fine_tuning, test_size=0.1, random_state=42)
Target_train.reset_index(inplace=True, drop=True)
Target_val.reset_index(inplace=True, drop=True)

source_ranges = {}
for source_name, source_df in Source_data.items():
    source_ranges[source_name] = [source_df['user_id'].min(), source_df['user_id'].max()]
source_range = list(source_ranges.values())

s_user_n = np.max([np.max(Source_train['user_id']), np.max(Source_test['user_id'])])
t_user_n = np.max([np.max(Target_fine_tuning['user_id']), np.max(Target_test['user_id'])])
item_n = np.max(item['item_id'])

def transform1(x, y, z, k, batch_size, **params):
    y_transformed = y + k * item_n

    dataset = TensorDataset(
        torch.tensor(x, dtype=torch.int64) - 1,
        torch.tensor(y, dtype=torch.int64) - 1,
        torch.tensor(y_transformed, dtype=torch.int64) - 1,
        torch.tensor(z, dtype=torch.float32)
    )
    return DataLoader(dataset, batch_size=batch_size, shuffle=True)


def transform2(x, y, z, batch_size, **params):
    dataset = TensorDataset(
        torch.tensor(x, dtype=torch.int64) - 1,
        torch.tensor(y, dtype=torch.int64) - 1,
        torch.tensor(z, dtype=torch.float32)
    )
    return DataLoader(dataset, batch_size=batch_size, shuffle=True)

s_train_set, s_test_set = [
    transform1(data["user_id"], data["item_id"], data["score"], data["Source_id"], batch_size)
    for data in [Source_train, Source_test]
]

t_train_set, t_val_set, t_test_set = [
    transform2(data["user_id"], data["item_id"], data["score"], batch_size)
    for data in [Target_train, Target_val, Target_test]
]

logging.getLogger().setLevel(logging.INFO)

cdm = MIRT(s_user_n, t_user_n, item_n, latent_dim,
           pp_dim, source_range, model_file, target_model_file)

if if_source_train == 1:
    with open("record.txt", "a") as f:
        f.write(f"------------------------------------------------------\n"
                f"MIRT Source domain training\n"
                f"Source: {source}, Target: {target}\n")
    cdm.Source_train(s_train_set, s_test_set, device="cuda")
    with open("record.txt", "a") as f:
        f.write("-------------------------------------------------------\n")
    logging.info("Source domain training completed. ")

if if_target_migration == 1:
    cdm.Transfer_parameters(cdm.t_irt_net, source_range)
    logging.info("Transfer parameters to the target domain completed.")
    with open("record.txt", "a") as f:
        f.write(f"------------------------------------------------------\n"
                f"MIRT--our\n"
                f"Source: {source}, Target: {target}\n")
    cdm.Target_train(cdm.t_irt_net, t_train_set, t_test_set, epoch=100, device="cuda")
    logging.info("Target domain training completed.")
    cdm.t_irt_net.load_state_dict(torch.load(target_model_file))
    auc, accuracy, rmse, f1 = cdm.Target_test(cdm.t_irt_net,t_test_set)
    with open("record.txt", "a") as f:
        f.write("Best AUC: %.6f, Best Accuracy: %.6f, Best RMSE: %.6f, Best F1: %.6f\n" % (
            auc, accuracy, rmse, f1))
        f.write("-------------------------------------------------------\n")

elif if_target_migration == 2:
    cdm.Transfer_parameters(cdm.t_irt_net2, source_range)
    logging.info("Transfer parameters to the target domain completed.")
    with open("record.txt", "a") as f:
        f.write(f"-------------------------------------------------------\n"
                f"MIRT-our++\n"
                f"Source: {source}, Target: {target}\n")
    cdm.Target_train(cdm.t_irt_net2, t_train_set, t_test_set, epoch=100, device="cuda")
    logging.info("Target domain training completed.")
    cdm.t_irt_net2.load_state_dict(torch.load(target_model_file))
    auc, accuracy, rmse, f1 = cdm.Target_test(cdm.t_irt_net2,t_test_set)
    with open("record.txt", "a") as f:
        f.write("Best AUC: %.6f, Best Accuracy: %.6f, Best RMSE: %.6f, Best F1: %.6f\n" % (
            auc, accuracy, rmse, f1))
        f.write("-------------------------------------------------------\n")
else:
    with open("record.txt", "a") as f:
        f.write(f"-------------------------------------------------------\n"
                f"MIRT-Origin\n"
                f"Source: {source}, Target: {target}\n")
    cdm.Target_train(cdm.t_irt_net , t_train_set, t_test_set, epoch=100, device="cuda")
    logging.info("Target domain training completed.")
    cdm.t_irt_net.load_state_dict(torch.load(target_model_file))
    auc, accuracy, rmse, f1 = cdm.Target_test(cdm.t_irt_net,t_test_set)
    with open("record.txt", "a") as f:
        f.write("Best AUC: %.6f, Best Accuracy: %.6f, Best RMSE: %.6f, Best F1: %.6f\n" % (
            auc, accuracy, rmse, f1))
        f.write("-------------------------------------------------------\n")