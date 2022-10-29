import torch
from torch import nn
from .U_Net import U_Net
from .helpers.image_rescale import reduce_image, unreduce_image






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
        
        self.T = torch.tensor(T)
        self.beta_sched = beta_sched
        self.inCh = inCh
        
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
            
            
            
    # Used to get the value of beta, a and a_bar from the schedulers
    # Inputs:
    #   t - Batch of t values of shape (N)
    # Outputs:
    #   Batch of beta and a values:
    #     beta_t
    #     a_t
    #     a_bar_t
    def get_scheduler_info(self, t):
        # Values depend on the scheduler
        if self.beta_sched == "cosine":
            # Beta_t, a_t, and a_bar_t
            # using the cosine scheduler
            a_bar_t = self.beta_sched_funct(t)
            a_bar_t1 = self.beta_sched_funct(t-1)
            beta_t = 1-(a_bar_t/(a_bar_t1))
            a_t = 1-beta_t
        else:
            # Beta_t, a_t, and a_bar_t
            # using the linear scheduler
            beta_t = self.beta_sched_funct[:t]
            a_t = 1-beta_t
            a_bar_t = torch.prod(a_t, dim=-1)
            
        return beta_t, a_t, a_bar_t
    
    
    # Unsqueezing n times along the given dim.
    # Note: dim can be 0 or -1
    def unsqueeze(self, X, dim, n):
        if dim == 0:
            return X.reshape(n*(1,) + X.shape)
        else:
            return X.reshape(X.shape + (1,)*n)
    
        
    # Used to noise a batch of images by t timesteps
    # Inputs:
    #   X - Batch of images of shape (N, C, L, W)
    #   t - Batch of t values of shape (N)
    # Outputs:
    #   Batch of noised images of shape (N, C, L, W)
    #   Batch of noised images of shape (N, C, L, W)
    #   Noise added to the images of shape (N, C, L, W)
    def noise_batch(self, X, t):
        # Make sure t isn't too large
        t = torch.min(t, self.T)
        
        # Sample gaussian noise
        epsilon = torch.randn_like(X)
        
        # The value of a_bar_t at timestep t depending on the scheduler
        a_bar_t = self.unsqueeze(self.get_scheduler_info(t)[2], -1, 3)
        t = self.unsqueeze(t, -1, 3)
        
        # Noise the images
        return torch.sqrt(a_bar_t)*X + torch.sqrt(1-a_bar_t)*epsilon, epsilon
    
    
    
    # Get the noise for a batch of images
    # Inputs:
    #   noise_shape - Shape of desired tensor of noise
    #   t - Batch of t values of shape (N)
    # Outputs:
    #   epsilon - Batch of noised images of the given shape
    def sample_noise(self, noise_shape, t):
        # Make sure t isn't too large
        t = torch.min(t, self.T)
        
        # Sample gaussian noise
        epsilon = torch.randn(noise_shape)
        
        return epsilon
    
    
    # Used to convert a batch of noise predictions to
    # a batch of mean predictions
    # Inputs:
    #   epsilon - The epsilon value for the mean of shape (N, C, L, W)
    #   x_t - The image to unoise of shape (N, C, L, W)
    #   t - A batch of t values for the beta schedulers of shape (N)
    # Outputs:
    #   A tensor of shape (N, C, L, W) representing the mean of the
    #     unnoised image x_t-1
    def noise_to_mean(self, epsilon, x_t, t):
        # Get the beta and a values for the batch of t values
        beta_t, a_t, a_bar_t = self.get_scheduler_info(t)
        beta_t = self.unsqueeze(beta_t, -1, 3)
        a_t = self.unsqueeze(a_t, -1, 3)
        a_bar_t = self.unsqueeze(a_bar_t, -1, 3)
        
        # Calculate the mean and return it
        return (1/torch.sqrt(a_t))*(x_t - (beta_t/torch.sqrt(1-a_bar_t))*epsilon)
    
    
    
    # Used to convert a batch of predicted v values to
    # a batch of variance predictions
    def vs_to_variance(self, v, t):
        # Get the beta values for this batch of ts
        beta_t, _, a_bar_t = self.get_scheduler_info(t)
        
        # Beta values for the previous value of t
        _, _, a_bar_t1 = self.get_scheduler_info(t-1)
        
        # Get the beta tilde value
        beta_tilde_t = ((1-a_bar_t1)/(1-a_bar_t))*beta_t
        
        beta_t = self.unsqueeze(beta_t, -1, 3)
        beta_tilde_t = self.unsqueeze(beta_tilde_t, -1, 3)
        
        # Return the variance value
        return torch.exp(v*torch.log(beta_t) + (1-v)*torch.log(beta_tilde_t))
        
        
        
    # Input:
    #   x_t - Batch of images of shape (B, C, L, W)
    # Outputs:
    #   noise - Batch of noise predictions of shape (B, C, L, W)
    #   vs - Batch of v matrix predictions of shape (B, C, L, W)
    def forward(self, x_t):
        # Send the input through the U-net to get
        # the mean and std of the gaussian distributions
        # for the image x_t-1
        out = self.unet(x_t)
        
        # Get the noise prediction from the output
        noise = out[:, :self.inCh]
        
        # Get the v values from the model (note, these
        # are used for the variances)
        vs = out[:, self.inCh:]
        
        return noise, vs
    
    
    
    # Given the mean, variance, and input for a normal distribution,
    # return the output value of the input in the distribution
    # Inputs:
    #   x - Input into the distribution
    #   mean - Distribution mean
    #   var - DIstribution variance
    #   - Note: x, mean, and var should have the same shape
    # Outputs:
    #   Distribution applied to x of the same shape as x
    def normal_dist(self, x, mean, var):
        return (1/(var*torch.sqrt(torch.tensor(2*torch.pi))))*torch.exp((-1/2)*((x-mean)/var)**2)
    
    
    
    # Given a batch of images, unoise them using the current models's state
    # Inputs:
    #   x_t - Batch of images at the given value of t of shape (N, C, L, W)
    #   t - Batch of t values of shape (N) or a single t value
    # Outputs:
    #   Image of shape (N, C, L, W) at timestep t-1, unnoised by one timestep
    def unnoise_batch(self, x_t, t):
        
        # Scale the image to (-1, 1)
        if x_t.max() <= 1.0:
            x_t = reduce_image(x_t)
        
        if type(t) == int or type(t) == float:
            t = torch.tensor(t).repeat(x_t.shape[0]).to(torch.long)
        elif type(t) == list and type(t[0]) == int:
            t = torch.tensor(t).to(torch.long)
        elif type(t) == torch.Tensor:
            if len(t.shape) == 0:
                t = t.repeat(x_t.shape[0]).to(torch.long)
        else:
            print(f"t values must either be a scalar, list of scalars, or a tensor of scalars, not type: {type(t)}")
            return
        
        # Get the model predictions
        noise_t, vs = self.forward(x_t)
        
        # Convert the noise to a mean and
        # the vs to variances
        mean_t = self.noise_to_mean(noise_t, x_t, t)
        var_t = self.vs_to_variance(vs, t)
        
        # Get the output of the predicted normal distribution
        out = self.normal_dist(x_t, mean_t, var_t)
        
        # Return the image scaled to (0, 255)
        return unreduce_image(out)