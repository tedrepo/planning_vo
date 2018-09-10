import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from copy import deepcopy
import time
import os, sys
import torch
import torch.nn as nn
from torch.nn.utils.clip_grad import clip_grad_norm
import torch.nn.functional as F
import torch.optim as optim
from torch.autograd import Variable
import numpy as np
import matplotlib.pyplot as plt
import torch.nn.init as init
from IPython import embed
import shutil
import torch # package for building functions with learnable parameters
import torch.nn as nn # prebuilt functions specific to neural networks
from torch.autograd import Variable # storing data while learning
from mdn_lstm import mdnLSTM
from utils import save_checkpoint, plot_strokes, get_dummy_data, DataLoader
rdn = np.random.RandomState(33)
# TODO one-hot the action space?

torch.manual_seed(139)
# David's blog post:
# https://github.com/hardmaru/pytorch_notebooks/blob/master/mixture_density_networks.ipynb
# input data should be - timestep, batchsize, features!
# Sampling example from tfbldr kkastner
# https://github.com/kastnerkyle/tfbldr/blob/master/examples/handwriting/generate_handwriting.py

def sample_2d(mu1, mu2, std1, std2, rho):
    cov = np.array([[std1*std1, std1*std2*rho],
                    [std1*std2*rho, std2*std2]])
    mean = np.array([mu1,mu2])
    x,y = rdn.multivariate_normal(mean, cov, 1)[0]
    return np.array([x,y])

def get_pi_idx(x, pdf):
    N = pdf.shape[0]
    accumulate = 0
    for i in range(N):
        accumulate+=pdf[i]
        if accumulate>=x:
            return i
    print("error sampling")
    return -1

def predict(x, h1_tm1, c1_tm1, h2_tm1, c2_tm1, batch_num=0, use_center=True):
    # one batch of x
    output, h1_tm1, c1_tm1, h2_tm1, c2_tm1 = lstm(x, h1_tm1, c1_tm1, h2_tm1, c2_tm1)
    # out_pi, out_mu1, out_mu2, out_sigma1, out_sigma2, out_corr
    out_pi, out_mu1, out_mu2, out_sigma1, out_sigma2, out_corr = lstm.get_mixture_coef(output)
    mso = (out_pi.cpu().data.numpy(),
           out_mu1.cpu().data.numpy(), out_mu2.cpu().data.numpy(),
           out_sigma1.cpu().data.numpy(), out_sigma2.cpu().data.numpy(),
           out_corr.cpu().data.numpy())
    pi, mu1, mu2, sigma1, sigma2, corr = mso
    # choose mixture
    bn = batch_num
    idx = rdn.choice(np.arange(pi.shape[1]), p=pi[bn])
    pred = sample_2d(mu1[bn,idx], mu2[bn,idx], sigma1[bn,idx], sigma2[bn,idx], corr[bn,idx])
    if use_center:
        pred = np.array([mu1[bn,idx], mu2[bn,idx], 0])
    return pred, h1_tm1, c1_tm1, h2_tm1, c2_tm1

def generate(xbatch,ybatch,modelname, num=200, teacher_force_predict=True, use_center=False, bn=0):
    h1_tm1 = Variable(torch.zeros((batch_size, hidden_size))).to(DEVICE)
    c1_tm1 = Variable(torch.zeros((batch_size, hidden_size))).to(DEVICE)
    h2_tm1 = Variable(torch.zeros((batch_size, hidden_size))).to(DEVICE)
    c2_tm1 = Variable(torch.zeros((batch_size, hidden_size))).to(DEVICE)
    x = xbatch[:,bn].to(DEVICE)
    y = ybatch[:,bn].to(DEVICE)
    num = min(num, x.shape[0])
    last_x = x[0][None,:]
    strokes = np.zeros((num,output_size), dtype=np.float32)
    for i in range(num-1):
        pred, h1_tm1, c1_tm1, h2_tm1, c2_tm1 = predict(last_x, h1_tm1, c1_tm1, h2_tm1, c2_tm1, use_center=use_center)
        strokes[i+1] = pred[None,:]
            # override
        last_x = x[i+1,:][None,:]
        if not teacher_force_predict and (i > lead_in):
            last_x[:,-2:] = torch.FloatTensor(pred[None,:])
    base = '_gen_bn%02d'%bn
    if use_center:
        base = base+'_center'
    ytrue = y.cpu().data.numpy()
    if teacher_force_predict:
        fname = os.path.join(modelname.replace('.pkl', base+'_tf.png'))
        print("plotting teacher force generation: %s" %fname)
    else:
        fname = os.path.join(modelname.replace('.pkl', base+'.png'))
        print("plotting generation: %s" %fname)

    plot_strokes(strokes, ytrue, name=fname, pen=False)
    #embed()

if __name__ == '__main__':
    import argparse
    lead_in = 4
    batch_size = 1
    data_batch_size = 32
    seq_length = 200
    hidden_size = 1024
    savedir = 'models'
    number_mixtures = 20
    train_losses, test_losses, train_cnts, test_cnts = [], [], [], []

    img_savedir = 'predictions'
    cnt = 0
    default_model_loadname = 'mdn_2d_models/model_000000000080000.pkl'
    if not os.path.exists(savedir):
        os.makedirs(savedir)
    if not os.path.exists(img_savedir):
        os.makedirs(img_savedir)
    parser = argparse.ArgumentParser()
    parser.add_argument('model_loadname', default=default_model_loadname)
    parser.add_argument('-c', '--cuda', action='store_true', default=False)
    parser.add_argument('-uc', '--use_center', action='store_true', default=False, help='use means instead of sampling')
    parser.add_argument('-tf', '--teacher_force', action='store_true', default=False)
    parser.add_argument('--training', action='store_true', default=False, help='generate from training set rather than test set')
    parser.add_argument('-n', '--num',default=300, help='length of data to generate')
    parser.add_argument('-bn', '--batch_num',type=int, default=0, help='index into batch from teacher force to use')
    parser.add_argument('--whole_batch', action='store_true', default=False, help='plot an entire batch')
    parser.add_argument('--num_plot', default=10, type=int, help='number of examples from training and test to plot')

    args = parser.parse_args()

    if args.cuda:
        DEVICE = 'cuda'
    else:
        DEVICE = 'cpu'
    data_loader = DataLoader(train_load_path='../data/train_2d_controller.npz',
                                 test_load_path='../data/train_2d_controller.npz',
                                 batch_size=data_batch_size)
    if args.training:
        xnp,ynp = data_loader.next_batch()
    else:
        xnp,ynp = data_loader.validation_data()
    output_size = ynp.shape[2]
    input_size = xnp.shape[2]
    x = Variable(torch.FloatTensor(xnp))
    y = Variable(torch.FloatTensor(ynp))

    lstm = mdnLSTM(input_size=input_size, hidden_size=hidden_size, number_mixtures=number_mixtures).to(DEVICE)
    model_save_name = 'model'
    if not os.path.exists(args.model_loadname):
        print("load model: %s does not exist"%args.model_loadname)
        sys.exit()
    else:
        print("loading %s" %args.model_loadname)
        lstm_dict = torch.load(args.model_loadname)
        lstm.load_state_dict(lstm_dict['state_dict'])
        train_cnts = lstm_dict['train_cnts']
        train_losses = lstm_dict['train_losses']
        test_cnts = lstm_dict['test_cnts']
        test_losses = lstm_dict['test_losses']

    if args.batch_num > data_loader.batch_size:
        args.batch_num = 0

    if not args.whole_batch:
        generate(x,y,args.model_loadname, num=args.num, teacher_force_predict=args.teacher_force, use_center=args.use_center, bn=args.batch_num)
    else:
        for bn in range(data_loader.batch_size):
            generate(x,y,args.model_loadname, num=args.num, teacher_force_predict=args.teacher_force, use_center=args.use_center, bn=bn)

