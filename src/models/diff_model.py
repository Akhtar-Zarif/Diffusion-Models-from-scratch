import torch
from torch import nn
from .U_Net import U_Net






class diff_model(nn.Module):
    # inCh - Number of input channels in the input batch
    # embCh - Number of channels to embed the batch to
    # chMult - Multiplier to scale the number of channels by
    #          for each up/down sampling block
    # num_heads - Number of heads in each multi-head non-local block
    # num_res_blocks - Number of residual blocks on the up/down path
    # T - Max number of diffusion steps
    # beta_sched - Scheduler for the beta noise term (linear or cosine)
    # useDeep - True to use deep residual blocks, False to use not deep residual blocks
    def __init__(self, inCh, embCh, chMult, num_heads, num_res_blocks,
                 T, beta_sched, useDeep=False):
        super(diff_model, self).__init__()
        
        self.T = T
        self.beta_sched = beta_sched
        
        # U_net model
        self.unet = U_Net(inCh, inCh*2, embCh, chMult, num_heads, num_res_blocks, useDeep)
        
        # What scheduler should be used to add noise
        # to the data?
        if self.beta_sched == "cosine":
            def f(t):
                s = 0.008
                return torch.cos(((t/T + s)/(1+s)) * torch.pi/2)**2 /\
                    torch.cos(torch.tensor((s/(1+s)) * torch.pi/2))**2
            self.beta_sched_funct = f
        else: # Linear
            self.beta_sched_funct = torch.linspace(1e-4, 0.02, T)
        
    # Used to noise an image by t timesteps
    def noise_batch(self, X, t):
        # Make sure t isn't too large
        t = min(t, self.T)
        
        # Sample gaussian noise
        epsilon = torch.randn_like(X)
        
        # The value of a_bar_t at timestep t depending on the scheduler
        if self.beta_sched == "cosine":
            a_t_bar = self.beta_sched_funct(torch.tensor(t))
        else:
            a_t = 1-self.beta_sched_funct[:t] # 1-B_t
            a_t_bar = torch.prod(a_t, dim=-1) # Pi [a_s]
        
        # Noise the images
        return torch.sqrt(a_t_bar)*X + torch.sqrt(1-a_t_bar)*epsilon
        
        
        
    def forward(self, x_t):
        # Send the input through the U-net to get
        # the mean and std of the gaussian distributions
        # for the image x_t-1
        return self.unet(x_t)