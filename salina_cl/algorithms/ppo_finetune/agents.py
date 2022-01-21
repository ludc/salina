#
# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
#


import gym
import numpy as np
import torch
import torch.nn.functional as F
from brax.envs import _envs, create_gym_env, wrappers
from brax.envs.to_torch import JaxToTorchWrapper
from gym.wrappers import TimeLimit
from torch import nn
from torch.distributions.normal import Normal
from salina import Agent
from salina.agents import Agents

class BatchNorm(Agent):
    def __init__(self,input_dimension):
        super().__init__()
        self.bn=nn.BatchNorm1d(num_features=input_dimension[0])
    
    def forward(self, t=None, **kwargs):
        if not t is None:
            input = self.get(("env/env_obs", t))
            input=self.bn(input)
            self.set(("env/normalized_env_obs", t), input)
        else:
            for t in range(self.workspace.time_size()):
                self.forward(t=t) 
            
class Normalizer(Agent):
    def __init__(self, input_dimension):
        super().__init__()        
        self.n_features = input_dimension[0]
        self.n = torch.nn.Parameter(torch.zeros(self.n_features),requires_grad=False)
        self.mean = torch.nn.Parameter(torch.zeros(self.n_features),requires_grad=False)
        self.mean_diff = torch.nn.Parameter(torch.zeros(self.n_features),requires_grad=False)

    def forward(self, t=None, **kwargs):
        if not t is None:
            input = self.get(("env/env_obs", t))
            self.update(input)
            input = self.normalize(input)
            self.set(("env/normalized_env_obs", t), input.detach())
        else:
            input = self.get("env/env_obs")
            for t in range(self.workspace.time_size()):
                self.update(self.get(("env/env_obs", t)))            
            input = self.normalize(input)
            self.set("env/normalized_env_obs", input.detach())

    def update(self, x):
        self.n += 1.0
        last_mean = self.mean.clone()
        self.mean += (x - self.mean).mean(dim=0) / self.n
        self.mean_diff += (x - last_mean).mean(dim=0) * (x - self.mean).mean(dim=0)

    def normalize(self, inputs):
        var = torch.clamp(self.mean_diff / self.n, min=1e-2)
        obs_std = torch.sqrt(var.detach())
        return (inputs - self.mean.detach()) / obs_std

def clip_grad(parameters, grad):
    return (
        torch.nn.utils.clip_grad_norm_(parameters, grad)
        if grad > 0
        else torch.Tensor([0.0])
    )

def NormalizedActionAgent(input_dimension,output_dimension, n_layers, hidden_size):
    return Agents(BatchNorm(input_dimension),ActionAgent(input_dimension,output_dimension, n_layers, hidden_size,True))

class ActionAgent(Agent):
    def __init__(self, input_dimension,output_dimension, n_layers, hidden_size,use_normalized_obs=False):
        super().__init__()
        self.iname="env/env_obs"
        if use_normalized_obs:
            self.iname="env/normalized_env_obs"
            
        input_size = input_dimension[0]
        num_outputs = output_dimension[0]
        hs = hidden_size
        n_layers = n_layers
        hidden_layers = (
            [
                nn.Linear(hs, hs) if i % 2 == 0 else nn.ReLU()
                for i in range(2 * (n_layers - 1))
            ]
            if n_layers > 1
            else [nn.Identity()]
        )
        self.model = nn.Sequential(
            nn.Linear(input_size, hs),
            nn.ReLU(),
            *hidden_layers,
            nn.Linear(hs, num_outputs),
        )

    def forward(self, t=None, action_std=0.0, **kwargs):
        if not self.training: assert action_std==0.0

        if t is None:
            input = self.get(self.iname)
            mean = self.model(input)
            var = torch.ones_like(mean) * action_std + 0.000001
            dist = Normal(mean, var)
            action = self.get("action_before_tanh")
            logp_pi = dist.log_prob(action).sum(axis=-1)
            logp_pi -= (2 * (np.log(2) - action - F.softplus(-2 * action))).sum(axis=-1)
            self.set("action_logprobs", logp_pi)
        else:
            input = self.get((self.iname, t))
            mean = self.model(input)
            var = torch.ones_like(mean) * action_std + 0.000001
            dist = Normal(mean, var)
            action = dist.sample() if action_std > 0 else dist.mean
            self.set(("action_before_tanh", t), action)
            logp_pi = dist.log_prob(action).sum(axis=-1)
            logp_pi -= (2 * (np.log(2) - action - F.softplus(-2 * action))).sum(axis=-1)
            self.set(("action_logprobs", t), logp_pi)
            action = torch.tanh(action)
            self.set(("action", t), action)

class CriticAgent(Agent):
    def __init__(self, input_dimension, n_layers, hidden_size,use_normalized_obs=False):
        super().__init__()
        self.iname="env/env_obs"
        if use_normalized_obs:
            self.iname="env/normalized_env_obs"
            
        input_size = input_dimension[0]
        hs = hidden_size
        n_layers = n_layers
        hidden_layers = (
            [
                nn.Linear(hs, hs) if i % 2 == 0 else nn.ReLU()
                for i in range(2 * (n_layers - 1))
            ]
            if n_layers > 1
            else [nn.Identity()]
        )
        self.model_critic = nn.Sequential(
            nn.Linear(input_size, hs),
            nn.ReLU(),
            *hidden_layers,
            nn.Linear(hs, 1),
        )

    def forward(self, **kwargs):
        input = self.get(self.iname)
        critic = self.model_critic(input).squeeze(-1)
        self.set("critic", critic)
