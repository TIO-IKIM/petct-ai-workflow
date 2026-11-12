import numpy as np
import nibabel as nib
import glob
import torch
from torchvision.models import resnet18
from torch import nn

class NumpyToTensorTransform2:
    def __init__(self, resize=None, transform=None):
        self.resize = resize
        self.transform = transform

    def __call__(self, sample):
        # Convert numpy array to torch tensor
        tensor_sample = torch.from_numpy(sample).unsqueeze(0).float()  # Add channel dimension
        tensor_sample = torch.nn.functional.interpolate(tensor_sample.unsqueeze(0), size=self.resize, mode='bilinear', align_corners=False).squeeze(0)
        tensor_sample = normalize_zscore(tensor_sample)
        return tensor_sample    

def normalize_zscore(tensor):
    mean = torch.mean(tensor)
    std = torch.std(tensor)
    return (tensor - mean) / std   

def reorient_image(image_path):

    pet = nib.load(image_path)
    pet_arr = pet.get_fdata()
    code = nib.aff2axcodes(pet.affine)
    orientation = ['L','A','S']

    for (i,o) in enumerate(code):
        if code[i] != orientation[i]:
            print(i)
            pet_arr = np.flip(pet_arr,i)
    
    return pet_arr
                   
def get_mips(image):
    image = np.transpose(image,axes=(2,1,0))
    mip_cor = np.max(image[::-1,:],axis=1)
    mip_sag = np.max(image[::-1,:],axis=2)
    return mip_cor, mip_sag


class MipClassifier(nn.Module):
    def __init__(self):
        super(MipClassifier, self).__init__()
        
        # Coronal model (ResNet18)
        self.model_cor = resnet18(pretrained=False)
        self.model_cor.conv1 = nn.Conv2d(1, 64, kernel_size=(7, 7), stride=(2, 2), padding=(3, 3), bias=False)
        self.model_cor.fc = nn.Identity()
        
        # Sagittal model (ResNet18)
        self.model_sag = resnet18(pretrained=False)
        self.model_sag.conv1 = nn.Conv2d(1, 64, kernel_size=(7, 7), stride=(2, 2), padding=(3, 3), bias=False)
        self.model_sag.fc = nn.Identity()

        # Linear layers for fusion
        self.linear = nn.Sequential(
            nn.Linear(1024, 512),
            nn.ReLU(),
            nn.BatchNorm1d(512),
            nn.Dropout(p=0.5),
            nn.Linear(512, 128),
            nn.BatchNorm1d(128),
            nn.Dropout(p=0.5),
            nn.Linear(128, 1)
        )
    
    def forward(self, coronal_input, sagittal_input):

        with torch.no_grad():
            coronal_features = self.model_cor(coronal_input)
            sagittal_features = self.model_sag(sagittal_input)

            combined_features = torch.cat((coronal_features, sagittal_features), dim=1)
            output = self.linear(combined_features)
        
        return output

                    
def run_classification(mip_cor,mip_sag, ckpt_path):
  
    model = MipClassifier()
    model.load_state_dict(torch.load(ckpt_path))
    model = model.to('cuda')
    model.eval()
    
    mip_cor = NumpyToTensorTransform2(resize=(224,224))(mip_cor).to('cuda')
    mip_cor = mip_cor.unsqueeze(0)
    mip_sag = NumpyToTensorTransform2(resize=(224,224))(mip_sag).to('cuda')
    mip_sag = mip_sag.unsqueeze(0) 
    logit = model(mip_cor, mip_sag)
    pred = (torch.sigmoid(logit)>0.5).float()
    pred_tracer = 'fdg' if pred == 0 else 'psma'
    
    return pred_tracer

def classify_pet(image_path, ckpt_path):
    
    new_image = reorient_image(image_path)
    mip_cor, mip_sag = get_mips(new_image)
    tracer = run_classification(mip_cor, mip_sag, ckpt_path)
    
    return tracer
