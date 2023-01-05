import torch
from .models.diff_model import diff_model
import matplotlib.pyplot as plt
import matplotlib.animation as animation


def infer():
    #### Parameters

    ## Loading params
    loadDir = "models/"
    loadFile = "model_50000.pkl"
    loadDefFile = "model_params_30000.json"

    ## Generation paramters
    step_size = 100                # Step size to take when generating images
    DDIM_scale = 0          # Scale to transition between a DDIM, DDPM, or in between.
                            # use 0 for pure DDIM and 1 for pure DDPM
    device = "cpu"
    w = 0.1                 # (only used if the model uses class info) 
                            # Classifier guidance scale factor
                            # Use 0 for no classifier guidance.
    class_label = -1         # (only used if the model uses class info) 
                            # Class we want the model to generate
                            # Use -1 to generate without a class
    
    
    
    
    
    ### Model Creation

    # Create a dummy model
    model = diff_model(3, 3, 1, 1, 100000, "cosine", 100, device, 100, 1000, 0.0, step_size, DDIM_scale)
    
    # Load in the model weights
    model.loadModel(loadDir, loadFile, loadDefFile,)
    
    # Sample the model
    noise, imgs = model.sample_imgs(1, class_label, w, True, True, True)
            
    # Convert the sample image to 0->255
    # and show it
    plt.close('all')
    plt.axis('off')
    noise = torch.clamp(noise.cpu().detach().int(), 0, 255)
    for img in noise:
        plt.imshow(img.permute(1, 2, 0))
        plt.savefig("fig.png", bbox_inches='tight', pad_inches=0, )
        plt.show()

    # Image evolution gif
    plt.close('all')
    fig, ax = plt.subplots()
    ax.set_axis_off()
    for i in range(0, len(imgs)):
        title = plt.text(imgs[i].shape[0]//2, -5, f"t = {i}", ha='center')
        imgs[i] = [plt.imshow(imgs[i], animated=True), title]
    animate = animation.ArtistAnimation(fig, imgs, interval=1, blit=True, repeat_delay=1000)
    animate.save('diffusion.gif', writer=animation.PillowWriter(fps=50))
    # plt.show()
    
    
    
    
    
if __name__ == '__main__':
    infer()