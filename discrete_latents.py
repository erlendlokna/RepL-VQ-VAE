import numpy as np
#from scipy.cluster.vq import whiten, kmeans, vq
import matplotlib.pyplot as plt
from statistics import mode
from src.models.encoder_decoder import VQVAEEncoder, VQVAEDecoder
from src.models.vq import VectorQuantize

from src.utils import (compute_downsample_rate,
                       get_root_dir,
                        time_to_timefreq,
                        timefreq_to_time,
                        quantize,
                        freeze)

from src.models.base_model import BaseModel, detach_the_unnecessary
from supervised_FCN.example_pretrained_model_loading import load_pretrained_FCN
from torch.utils.data import DataLoader
import torch.nn.functional as F
import pytorch_lightning as pl
import torch
from torch import nn
from torch.nn import init
import torch.nn.functional as F
from torch.optim.lr_scheduler import CosineAnnealingLR

from sklearn import metrics
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans, SpectralClustering
from sklearn.model_selection import train_test_split
from sklearn.svm import SVC

from pathlib import Path
import tempfile
from tqdm import tqdm


class Base:
    #Untrained instance
    def __init__(self,
                input_length,
                config):
        self.input_length = input_length
        self.config = config
        self.n_fft = config['VQVAE']['n_fft']

        dim = config['encoder']['dim']
        in_channels = config['dataset']['in_channels']

        downsampled_width = config['encoder']['downsampled_width']
        downsampled_rate = compute_downsample_rate(input_length, self.n_fft, downsampled_width)

        self.encoder = VQVAEEncoder(dim, 2*in_channels, downsampled_rate, config['encoder']['n_resnet_blocks'])
        self.vq_model = VectorQuantize(dim, config['VQVAE']['codebook']['size'], **config['VQVAE'])
        self.decoder = VQVAEDecoder(dim, 2 * in_channels, downsampled_rate, config['decoder']['n_resnet_blocks'])
    
    def encode_to_z_q(self, x):
        """
        x: (B, C, L)
        """
        C = x.shape[1]
        xf = time_to_timefreq(x, self.n_fft, C)  # (B, C, H, W)
        
        z = self.encoder(xf)  # (b c h w)
        z_q, indices, vq_loss, perplexity = quantize(z, self.vq_model)  # (b c h w), (b (h w) h), ...
        return z_q, indices

    def forward(self, x):
        u = time_to_timefreq(x, self.n_fft, x.shape[1])
        z = self.encoder(u)
        if not self.decoder.is_upsample_size_updated:
                self.decoder.register_upsample_size(torch.IntTensor(np.array(u.shape[2:])))
        z_q, _, _, _ = quantize(z, self.vq_model)
        u_hat = self.decoder(z_q)
        x_hat = timefreq_to_time(u_hat, self.n_fft, x.shape[1])
        return x_hat.detach()
    
    def validate(self, data_loader, vizualise=True):
        #checking mean absolute error for each ts in data_loader
        dataloader_iterator = iter(data_loader)
        number_of_batches = len(data_loader)

        mae = []
        plot = np.array([False] * number_of_batches)
        plot[np.random.randint(0, number_of_batches)] = True

        for i in range(number_of_batches):
            try:
                x, y = next(dataloader_iterator)
            except StopIteration:
                dataloader_iterator = iter(data_loader)
                x, y = next(dataloader_iterator)
            
            x_hat = self.forward(x)
            for j in range(len(x)):
                #checking each ts
                mae.append(
                    metrics.mean_absolute_error(x[j], x_hat[j])
                )
                if plot[i] and vizualise:
                    plot[i] = False
                    f, a = plt.subplots()
                    a.plot(x[j].squeeze().numpy(), label="x")
                    a.plot(x_hat[j].squeeze().numpy(), label="x_hat")
                    plt.show()

        return mae

    # ---- discrete latent variable extraction ----
    def run_through_encoder_codebook(self, data_loader, flatten=False, max_pool=False):
        #collecting all the timeseries codebook index representations:
        dataloader_iterator = iter(data_loader)
        number_of_batches = len(data_loader)

        zqs_list = [] #TODO: make static. List containing zqs for each timeseries in data_loader
        s_list = []

        for i in range(number_of_batches):
            try:
                x, y = next(dataloader_iterator)
            except StopIteration:
                dataloader_iterator = iter(data_loader)
                x, y = next(dataloader_iterator)
            
            z_q, s = self.encode_to_z_q(x)

            for i, zq_i in enumerate(z_q):    
                zqs_list.append(zq_i.detach().tolist())
                s_list.append(s[i].tolist())

        zqs_tensor = torch.tensor(zqs_list, dtype=torch.float64)
        s_tensor = torch.tensor(s_list, dtype=torch.int32)
        return zqs_tensor, s_tensor
    
    def get_flatten_zqs_s(self, data_loader):
        zqs, s = self.run_through_encoder_codebook(data_loader)
        zqs = torch.flatten(zqs, start_dim = 1)
        s = torch.flatten(s, start_dim = 1)
        return zqs, s

    def get_max_pooled_zqs(self, data_loader, kernel_size=2, stride=2, flatten=True):
        zqs, s = self.run_through_encoder_codebook(data_loader)
        max_pooling_layer = torch.nn.MaxPool2d(kernel_size=kernel_size, stride=stride)
        pooled_zqs = max_pooling_layer(zqs)
        return pooled_zqs
    
    def get_avg_pooled_zqs(self, data_loader, kernel_size=2, stride=2, flatten=True):
        zqs, s = self.run_through_encoder_codebook(data_loader)
        avg_pooling_layer = torch.nn.AvgPool2d(kernel_size = kernel_size, stride = stride)
        pooled_zqs = avg_pooling_layer(zqs)
        return pooled_zqs
    
    def get_global_avg_pooled_zqs(self, data_loader, kernel_size=2, stride=2):
        zqs, s = self.run_through_encoder_codebook(data_loader)
        return zqs.mean(dim=(-2, -1))

    def get_global_max_pooled_zqs(self, data_loader, kernel_size=2, stride=2):
        zqs, s = self.run_through_encoder_codebook(data_loader)
        zqs_mp =  F.max_pool2d(zqs, kernel_size=zqs.size()[2:])
        zqs_mp = torch.flatten(zqs_mp, start_dim = 1)
        return zqs_mp

    def get_conv2d_zqs(self, data_loader, in_channels, out_channels, kernel_size, stride, padding):
        zqs, s = self.run_through_encoder_codebook(data_loader)
        zqs = torch.tensor(zqs, dtype=torch.float)

        conv = torch.nn.Conv2d(in_channels, out_channels,
                               kernel_size, stride, padding)
        relu = torch.nn.LeakyReLU()
        avgpool = torch.nn.AvgPool2d(2, 2)

        new_zqs = conv(zqs)
        new_zqs = relu(new_zqs)

        zqs_conv = torch.flatten(new_zqs, start_dim=1)
        return zqs_conv.detach()

    def get_codebook(self):
        return self.vq_model.codebook
    

class PretrainedLatents(Base):
    #pretrained loader
    def __init__(self,
                input_length,
                config,
                contrastive=False):
        super().__init__(input_length, config)
        
        #grabbing pretrained models:
        dataset_name = config['dataset']['dataset_name']
        if contrastive:
            self.load(self.encoder, get_root_dir().joinpath('saved_models'), f'contrastive_encoder-{dataset_name}.ckpt')
            self.load(self.decoder, get_root_dir().joinpath('saved_models'), f'contrastive_decoder-{dataset_name}.ckpt')
            self.load(self.vq_model, get_root_dir().joinpath('saved_models'), f'contrastive_vq_model-{dataset_name}.ckpt')
        else:
            self.load(self.encoder, get_root_dir().joinpath('saved_models'), f'encoder-{dataset_name}.ckpt')
            self.load(self.decoder, get_root_dir().joinpath('saved_models'), f'decoder-{dataset_name}.ckpt')
            self.load(self.vq_model, get_root_dir().joinpath('saved_models'), f'vq_model-{dataset_name}.ckpt')


    def load(self, model, dirname, fname):
        """
        model: instance
        path_to_saved_model_fname: path to the ckpt file (i.e., trained model)
        """
        try:
            model.load_state_dict(torch.load(dirname.joinpath(fname)))
            print(f"{fname} loaded..")
        except FileNotFoundError:
            print(fname + ". Not found..")
            dirname = Path(tempfile.gettempdir())
            model.load_state_dict(torch.load(dirname.joinpath(fname)))

def randomize_model(model):
        def weights_init(m):
            if hasattr(m, 'weight') and (isinstance(m, (nn.Conv2d, nn.ConvTranspose2d)) or isinstance(m, nn.Linear)):
                init.kaiming_normal_(m.weight)
                if m.bias is not None:
                    init.constant_(m.bias, 0)

        model.apply(weights_init)

        for module in model.modules():
            if isinstance(module, nn.BatchNorm2d):
                init.constant_(module.weight, 1)
                init.constant_(module.bias, 0)
                init.constant_(module.running_mean, 0)
                init.constant_(module.running_var, 1)
        
        return model

class RandomInitLatents(Base):
    def __init__(self,
                input_length,
                config,
                contrastive=False):
        super().__init__(input_length, config)

        self.encoder = randomize_model(self.encoder)
        self.decoder = randomize_model(self.decoder)

    
