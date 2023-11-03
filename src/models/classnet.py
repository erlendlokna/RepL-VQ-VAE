import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
import numpy as np
from src.utils import get_root_dir
import tempfile
from pathlib import Path

"""
For non linear probes. Not yet finished
"""



class CNNClassNet(nn.Module):
    def __init__(self, input_channels, input_height, input_width, num_classes=2, hidden_size=200, dropout_rate=0.1):
        super().__init__()
        
        # Convolutional layers
        self.conv1 = nn.Conv2d(input_channels, 32, kernel_size=3, padding=1)
        self.relu1 = nn.ReLU()
        self.maxpool1 = nn.MaxPool2d(kernel_size=2, stride=2)
        
        self.conv2 = nn.Conv2d(32, 64, kernel_size=3, padding=1)
        self.relu2 = nn.ReLU()
        self.maxpool2 = nn.MaxPool2d(kernel_size=2, stride=2)
        
        # Fully connected layers
        self.fc1 = nn.Linear(64 * (input_height // 4) * (input_width // 4), hidden_size)
        self.relu3 = nn.ReLU()
        self.batchnorm = nn.BatchNorm1d(hidden_size)
        self.dropout = nn.Dropout(p=dropout_rate)
        self.fc2 = nn.Linear(hidden_size, num_classes)
        
    def forward(self, x):
        x = self.conv1(x)
        x = self.relu1(x)
        x = self.maxpool1(x)
        
        x = self.conv2(x)
        x = self.relu2(x)
        x = self.maxpool2(x)
        
        x = x.view(x.size(0), -1)  # Flatten the output for the fully connected layers
        
        x = self.fc1(x)
        x = self.relu3(x)
        x = self.batchnorm(x)
        x = self.dropout(x)
        x = self.fc2(x)
        
        return x
    
    def predict(self,x):
        if isinstance(x, np.ndarray):
            x = torch.tensor(x, dtype=torch.float)
        p=self.forward(x)
        pred=p.argmax(dim=-1)
        return pred

def check_type(train_zqs):
    if isinstance(train_zqs, np.ndarray):
        train_zqs = torch.tensor(train_zqs, dtype=torch.float)
        if len(train_zqs.shape) > 2:
            train_zqs = torch.flatten(train_zqs, start_dim=1)
    elif len(train_zqs.shape) > 2:
        train_zqs = torch.flatten(train_zqs, start_dim=1)
    return train_zqs
class ClassNet(nn.Module):
    def __init__(self, input_length, num_classes):
        super(ClassNet, self).__init__()
        self.fc1= nn.Linear(input_length, 300)
        self.bn=nn.BatchNorm1d(300)
        self.dropout=nn.Dropout(0,1)
        self.fc2 = nn.Linear(300, num_classes)
        self.optimizer = optim.Adam(self.parameters(), lr=1e-3)

    def forward(self,x):
        h=F.relu(self.fc1(x))
        h=self.fc2(self.bn(self.dropout(h)))
        return torch.log_softmax(h,dim=-1)

    def predict(self,x):
        x = check_type(x)
        p=self.forward(x)
        pred=p.argmax(dim=-1)
        return pred
