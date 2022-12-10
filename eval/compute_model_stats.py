# Path hack for relative paths above parent
import sys, os
sys.path.insert(0, os.path.abspath('src/'))


import torch
from torch import nn
from torchvision import transforms
import math
import numpy as np

from src.models.diff_model import diff_model

cpu = torch.device("cpu")







def compute_model_stats():
    # Compute the mean and varaince of the model


    # Parameters
    model_dirname = "models"
    model_filename = "model_680000.pkl"
    model_params_filename = "model_params_680000.json"

    device = "gpu"

    num_fake_imgs = 1000
    batchSize = 350






    # Load in the model
    model = diff_model(1, 64, 1, 1, 1, 1, 1, 1, device, False, 0)
    model.loadModel(model_dirname, model_filename, model_params_filename)
    model.eval()

    # Load in the inception network
    inceptionV3 = torch.hub.load('pytorch/vision:v0.10.0', 'inception_v3', weights="Inception_V3_Weights.DEFAULT")
    inceptionV3.eval()
    inceptionV3.to(model.device)

    # Remove the fully connected output layer
    inceptionV3.fc = nn.Identity()
    inceptionV3.aux_logits = False
    


    ### Model Generation ###

    # Generate images and calculate the inceptions scores
    scores = None
    with torch.no_grad():
        for i in range(math.ceil(num_fake_imgs/batchSize)):
            # Get the current batch size
            cur_batch_size = min(num_fake_imgs, batchSize*(i+1))-batchSize*i

            # Generate some images
            imgs = model.sample_imgs(cur_batch_size, use_tqdm=True)

            # Resize the images to be of shape (3, 299, 299)
            imgs = transforms.Compose([transforms.Resize((299,299))])(imgs.to(torch.int8))

            # Calculate the inception scores and store them
            if type(scores) == type(None):
                scores = inceptionV3(imgs).to(cpu).numpy().astype(np.float16)
            else:
                scores = np.concatenate((scores, inceptionV3(imgs).to(cpu).numpy().astype(np.float16)), axis=0)
            

            print(f"Num loaded: {min(num_fake_imgs, batchSize*(i+1))}")
    
    # Delete the model as its no longer needed
    device = model.device
    del model, inceptionV3

    # Calculate the mean and covariance of the generated batch
    mean = np.mean(scores, axis=0)
    var = np.cov(scores, rowvar=False)


    # Save the mean and variance
    np.save("eval/saved_stats/fake_mean_new.npy", mean)
    np.save("eval/saved_stats/fake_var_new.npy", var)






if __name__ == "__main__":
    compute_model_stats()