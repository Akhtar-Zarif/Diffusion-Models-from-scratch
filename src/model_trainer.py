import torch
from torch import nn
from .helpers.image_rescale import reduce_image, unreduce_image
import numpy as np


cpu = torch.device('cpu')
gpu = torch.device('cuda:0')




# Trains a diffusion model
class model_trainer():
    # diff_model - A diffusion model to train
    # batchSize - Batch size to train the model with
    # epochs - Number of epochs to train the model for
    # lr - Learning rate of the model optimizer
    # device - Device to put the model and data on (gpu or cpu)
    # saveDir - Directory to save the model to
    # numSaveEpochs - Number of epochs until saving the models
    def __init__(self, diff_model, batchSize, epochs, lr, device, Lambda, saveDir, numSaveEpochs):
        # Saved info
        self.T = diff_model.T
        self.model = diff_model
        self.batchSize = batchSize
        self.epochs = epochs
        self.Lambda = Lambda
        self.saveDir = saveDir
        self.numSaveEpochs = numSaveEpochs
        
        # Convert the device to a torch device
        if device.lower() == "gpu":
            if torch.cuda.is_available():
                dev = device.lower()
                device = torch.device('cuda:0')
            else:
                dev = "cpu"
                print("GPU not available, defaulting to CPU. Please ignore this message if you do not wish to use a GPU\n")
                device = torch.device('cpu')
        else:
            dev = device.lower()
            device = torch.device('cpu')
        self.device = device
        self.dev = dev
        
        # Put the model on the desired device
        self.model.to(self.device)
            
        # Uniform distribution for values of t
        self.t_vals = np.arange(0, self.T.detach().cpu().numpy())
        self.T_dist = torch.distributions.uniform.Uniform(float(2.0), float(self.T-1))
        
        # Optimizer
        self.optim = torch.optim.Adam(self.model.parameters(), lr=lr)
        
        # Loss function
        self.KL = nn.KLDivLoss(reduction="none").to(device)


        # Loss cumulator for each value of t
        self.losses = np.zeros((self.T, 10))
        self.losses_ct = np.zeros(self.T, dtype=int)


    
    # Update the stored loss values for each value of t
    # Inputs:
    #   loss_vec - Vector of shape (batchSize) with the L_vlb loss
    #              for each item in the batch
    #   t - Vector of shape (batchSize) with the t values for each
    #       item in the batch
    def update_losses(self, loss_vec, t):
        # Iterate over all losses and values of t
        for t_val, loss in zip(t, loss_vec):
            # Save the loss value to the losses array
            if self.losses_ct[t_val] == 10:
                self.losses[t_val] = np.concatenate((self.losses[t_val][1:], [loss]))
            else:
                self.losses[t_val, self.losses_ct[t_val]] = loss
                self.losses_ct[t_val] += 1
        
        
    # Simple loss function (L_simple)
    # Inputs:
    #   epsilon - True epsilon values of shape (N, C, L, W)
    #   epsilon_pred - Predicted epsilon values of shape (N, C, L, W)
    # Outputs:
    #   Scalar loss value over the entire batch
    def loss_simple(self, epsilon, epsilon_pred):
        return torch.nn.functional.mse_loss(epsilon_pred, epsilon)


    # KL Divergence loss
    # Inputs:
    #   y_true - Distribution we want the model to predict
    #   y_pred - Predicted distribution the model predicted
    # Outputs:
    #   Scalar value of the KL divergence loss between the 2 distribution
    def KLDivergence(self, y_true, y_pred):
        # Handling small values
        y_true = torch.where(y_true < 1e-5, y_true+1e-5, y_true)
        y_pred = torch.where(y_pred < 1e-5, y_pred+1e-5, y_pred)
        return (y_true*(y_true.log() - y_pred.log())).mean()
    
    # Variational Lower Bound loss function
    # Inputs:
    #   x_t - The noised image at time t-1 of shape (N, C, L, W)
    #   q - The prior of the unnoised image at time t-1 of shape (N, C, L, W)
    #   mean_t - Predicted mean at time t of shape (N, C, L, W)
    #   var_t - Predicted variance at time t of shape (N, C, L, W)
    #   t - The value timestep of shape (N)
    # Outputs:
    #   Loss scalar over the entire batch
    def loss_vlb(self, x_t1, q, mean_t, var_t, t):
        # Using the mean and variance, send the noised image
        # at time x_t through the distribution with the
        # given mean and variance.
        # Note: The mean is detached so that L_vlb is essentially
        # the loss for only the variance
        x_t1_pred = self.model.normal_dist(x_t1, mean_t.detach(), var_t)
        x_t1_pred += 1e-5 # Residual for small probabilities
        
        # Convert the x_t-1 values to for easier notation
        p = x_t1_pred # Predictions
        
        # Depending on the value of t, get the loss
        loss = torch.where(t==0,
                    -torch.log(p).mean(-1).mean(-1).mean(-1),
                    self.KLDivergence(q, p)
        )

        # # Update the loss storage
        # t = t.detach().cpu().numpy()
        # # self.update_losses(loss.detach().cpu().numpy(), t)

        # # Have 10 loss values been sampled for each value of t?
        # if np.sum(self.losses_ct) == self.losses.size - 20:
        #     # The losses are based on the probability for each
        #     # value of t
        #     p_t = np.sqrt((self.losses**2).mean(-1))
        #     p_t = p_t / p_t.sum()
        #     loss = loss / torch.tensor(p_t[t], device=loss.device)
        # # Otherwise, don't change the loss values
            
        return loss.mean()
    
    
    
    # Loss for the variance
    def loss_variance(self, v, t):
        # Get the beta values for this batch of ts
        beta_t, _, a_bar_t = self.model.get_scheduler_info(t)
        
        # Beta values for the previous value of t
        _, _, a_bar_t1 = self.model.get_scheduler_info(t-1)
        
        # Get the beta tilde value
        beta_tilde_t = ((1-a_bar_t1)/(1-a_bar_t))*beta_t
        beta_tilde_t = self.model.unsqueeze(beta_tilde_t, -1, 3)
        
        # KL loss between the v values and t values
        # Depending on the value of t, get the loss
        loss = torch.where(t==0,
                    -torch.log(v).flatten(1,-1).sum(-1).mean(),
                    self.KL(v, beta_tilde_t).flatten(1,-1).sum(-1).mean()
        ).mean()
        
        return loss
    
    
    # Combined loss
    # Inputs:
    #   epsilon - True epsilon values of shape (N, C, L, W)
    #   epsilon_pred - Predicted epsilon values of shape (N, C, L, W)
    #   x_t - The noised image at time t of shape (N, C, L, W)
    #   x_t1 - The unnoised image at time t-1 of shape (N, C, L, W)
    #   t - The value timestep of shape (N)
    # Outputs:
    #   Loss as a scalar over the entire batch
    def lossFunct(self, epsilon, epsilon_pred, v, x_0, x_t, x_t1, t):
        # Get the mean and variance from the model
        mean_t_pred = self.model.noise_to_mean(epsilon_pred, x_t, t)
        var_t_pred = self.model.vs_to_variance(v, t)


        ### Preparing for the real normal distribution

        # Get the beta values for this batch of ts
        beta_t, a_t, a_bar_t = self.model.get_scheduler_info(t)
        
        # Beta values for the previous value of t
        _, _, a_bar_t1 = self.model.get_scheduler_info(t-1)

        # Unsqueezing the values to match shape
        beta_t = self.model.unsqueeze(beta_t, -1, 3)
        a_t = self.model.unsqueeze(a_t, -1, 3)
        a_bar_t = self.model.unsqueeze(a_bar_t, -1, 3)
        a_bar_t1 = self.model.unsqueeze(a_bar_t1, -1, 3)

        # Get the beta tilde value
        beta_tilde_t = ((1-a_bar_t1)/(1-a_bar_t))*beta_t

        # Get the true mean distribution
        mean_t = ((torch.sqrt(a_bar_t1)*beta_t)/(1-a_bar_t))*x_0 +\
            ((torch.sqrt(a_t)*(1-a_bar_t1))/(1-a_bar_t))*x_t

        # Get the prior
        q_x_t1 = self.model.normal_dist(x_t1, mean_t, beta_tilde_t)
        
        # Get the losses
        loss_simple = self.loss_simple(epsilon, epsilon_pred)
        loss_vlb = self.loss_vlb(x_t1, q_x_t1, mean_t_pred, var_t_pred, t)
        
        # Return the combined loss
        return loss_simple + self.Lambda*loss_vlb, loss_simple, self.Lambda*loss_vlb
        
    
    
    # Trains the saved model
    # Inputs:
    #   X - A batch of images of shape (B, C, L, W)
    def train(self, X):
        
        # Put the data on the cpu
        X = X.to(cpu)
        
        # Scale the image to (-1, 1)
        if X.max() > 1.0:
            X = reduce_image(X)
        
        for epoch in range(1, self.epochs+1):
            # Model saving
            if epoch%self.numSaveEpochs == 0:
                self.model.saveModel(self.saveDir, epoch)
            
            # Get a sample of `batchSize` number of images and put
            # it on the correct device
            batch_x_0 = X[torch.randperm(X.shape[0])[:self.batchSize]].to(self.device)
            
            # Get values of t to noise the data
            # Sample using weighted values if each t has 10 loss values
            if np.sum(self.losses_ct) == self.losses.size - 20:
                # Weights for each value of t
                p_t = np.sqrt((self.losses**2).mean(-1))
                p_t = p_t / p_t.sum()

                # Sample the vaues of t
                t_vals = torch.tensor(np.random.choice(self.t_vals, size=self.batchSize, p=p_t), device=batch_x_0.device)
            # Sample uniformly until we get to that point
            else:
                t_vals = self.T_dist.sample((self.batchSize,)).to(self.device)
                t_vals = torch.round(t_vals).to(torch.long)
            
            # Noise the batch to time t-1
            batch_x_t1, epsilon_t1 = self.model.noise_batch(batch_x_0, t_vals-1)
            
            # Noise the batch to time t
            batch_x_t, epsilon_t = self.model.noise_batch(batch_x_0, t_vals)
            
            # Send the noised data through the model to get the
            # predicted noise and variance for batch at t-1
            epsilon_t1_pred, v_t1_pred = self.model(batch_x_t, t_vals)
            
            # Get the loss
            loss, loss_mean, loss_var = self.lossFunct(epsilon_t, epsilon_t1_pred, v_t1_pred, 
                                  batch_x_0, batch_x_t, batch_x_t1, t_vals)
            # loss = self.loss_simple(epsilon_t, epsilon_t1_pred)
            
            # Optimize the model
            loss.backward()
            self.optim.step()
            self.optim.zero_grad()
            
            print(f"Loss at epoch #{epoch}  Combined: {loss.item()}    Mean: {loss_mean.item()}    Variance: {loss_var.item()}")